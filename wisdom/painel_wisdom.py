# =====================================================================
#  Painel Wisdom v2.1 - INTERFACE NO COMPUTADOR
#  Python 3 + Tkinter + PySerial (Thonny: interpretador "Python 3 local")
#  Requer: pyserial  (Thonny: Ferramentas > Gerenciar pacotes > pyserial)
#
#  Firmware na placa: PainelWisdom.ino (Arduino IDE) ou main.py
#  (MicroPython) - versao 2 dos dois, que recebem a pinagem pela USB.
#
#  - Cabe na tela de 7" do Raspberry Pi (800x480). F11 = tela cheia.
#  - Aba "Pinagem": assistente que descobre os pinos sem datasheet.
#    A pinagem fica salva em wisdom_pinos.json, ao lado deste arquivo.
#
#  Tio Rafa - @prof.tiorafa
# =====================================================================

import json
import math
import os
import queue
import random
import threading
import time
import tkinter as tk
import unicodedata
from collections import deque
from tkinter import ttk, colorchooser
from tkinter import font as tkfont

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

# ---------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------
BAUD = 115200
MAX_ADC = 65535
HIST_PONTOS = 120
ARQ_PINOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wisdom_pinos.json")

# GPIOs do ESP32 classico (mesma lista do firmware)
SAIDAS = [0, 2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 23, 25, 26, 27, 32, 33]
SO_ENTRADA = [34, 35, 36, 39]

# Funcoes da placa: chave do protocolo, nome na tela, aceita so-entrada?
FUNCOES = [
    ("LED0", "LED 1", False), ("LED1", "LED 2", False),
    ("LED2", "LED 3", False), ("LED3", "LED 4", False),
    ("RGBR", "RGB verm.", False), ("RGBG", "RGB verde", False),
    ("RGBB", "RGB azul", False),
    ("BTN", "Botao", True), ("POT", "Potenc.", True),
    ("SOM", "Microfone", True), ("SERVO", "Servo", False),
    ("RELE", "Rele", False),
]
OPCOES = [("LEDINV", 0), ("RGBINV", 0), ("BTNLOW", 1), ("OLEDSH", 0)]

# Pinagem padrao da BlackBoard Wisdom (descoberta com o assistente numa placa real)
PINAGEM_PADRAO = {"LED0": 13, "LED1": 4, "LED2": 16, "LED3": 17,
                  "RGBR": 19, "RGBG": 23, "RGBB": 18,
                  "BTN": 27, "POT": 39, "SOM": 36, "SERVO": -1, "RELE": -1}

NOMES_I2C = {0x3C: "OLED", 0x3D: "OLED", 0x40: "HTU21D (temp/umid)",
             0x1C: "MMA8452Q (acel.)", 0x1D: "MMA8452Q (acel.)",
             0x39: "APDS9960 (luz/prox)"}

COR_FUNDO = "#14161c"
COR_PAINEL = "#1e2129"
COR_BORDA = "#2c303b"
COR_TEXTO = "#e8eaf0"
COR_SUAVE = "#8b90a0"
COR_DESTAQUE = "#ffcc00"
COR_OK = "#3ddc84"
COR_ERRO = "#ff5c5c"
CORES_BARGRAPH = ["#3ddc84", "#3ddc84", "#ffcc00", "#ff5c5c"]


def sem_acentos(texto):
    """O OLED so tem fonte ASCII."""
    norm = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in norm if ord(c) < 128)


def converter_campos(texto):
    """'pot=123;btn=0;t=null' -> {'pot': 123.0, 'btn': 0.0, 't': None}"""
    dados = {}
    for par in texto.split(";"):
        if "=" not in par:
            continue
        k, v = par.split("=", 1)
        try:
            dados[k] = None if v == "null" else float(v)
        except ValueError:
            dados[k] = v
    return dados


def carregar_pinos():
    cfg = dict(PINAGEM_PADRAO)
    cfg.update(dict(OPCOES))
    try:
        with open(ARQ_PINOS, encoding="utf-8") as f:
            salvo = json.load(f)
        for k in cfg:
            if k in salvo:
                cfg[k] = int(salvo[k])
    except (OSError, ValueError):
        pass
    return cfg


# =====================================================================
#  Conexao serial em thread separada (a tela nunca trava)
# =====================================================================
class ConexaoSerial:
    def __init__(self):
        self.porta = None
        self.fila = queue.Queue()
        self._rodando = False

    @staticmethod
    def listar():
        if serial is None:
            return []
        return [(p.device, p.description) for p in serial.tools.list_ports.comports()]

    def conectar(self, nome):
        s = serial.Serial()
        s.port = nome
        s.baudrate = BAUD
        s.timeout = 0.1
        s.dtr = False      # evita resetar o ESP32 ao abrir a porta
        s.rts = False
        s.open()
        self.porta = s
        self._rodando = True
        threading.Thread(target=self._ler, daemon=True).start()

    def desconectar(self):
        self._rodando = False
        if self.porta:
            try:
                self.porta.close()
            except Exception:
                pass
        self.porta = None

    @property
    def conectado(self):
        return self.porta is not None and self.porta.is_open

    def enviar(self, comando):
        if not self.conectado:
            return False
        try:
            self.porta.write((sem_acentos(comando) + "\n").encode("ascii", "ignore"))
            return True
        except Exception as e:
            self.fila.put(("ERRO_PORTA", str(e)))
            return False

    def _ler(self):
        resto = b""
        while self._rodando:
            try:
                bloco = self.porta.read(256)
            except Exception as e:
                self.fila.put(("ERRO_PORTA", str(e)))
                break
            if not bloco:
                continue
            resto += bloco
            while b"\n" in resto:
                linha, resto = resto.split(b"\n", 1)
                self.fila.put(("LINHA", linha.decode("utf-8", "ignore").strip()))


# =====================================================================
#  Aplicacao
# =====================================================================
class PainelWisdom(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Painel Wisdom - Tio Rafa")
        self.configure(bg=COR_FUNDO)

        # Tela pequena (7" = 800x480) -> modo compacto
        self.compacto = self.winfo_screenheight() <= 600
        c = self.compacto
        # Fontes que existem no Windows e no Raspberry Pi OS
        familias = set(tkfont.families(self))
        sans = next((f for f in ("Segoe UI", "DejaVu Sans", "Liberation Sans", "Arial") if f in familias), "TkDefaultFont")
        mono = next((f for f in ("Consolas", "DejaVu Sans Mono", "Liberation Mono", "Courier New") if f in familias), "TkFixedFont")
        self.F = {
            "base": (sans, 8 if c else 10),
            "negrito": (sans, 8 if c else 10, "bold"),
            "titulo": (sans, 9 if c else 11, "bold"),
            "valor": (mono, 13 if c else 20, "bold"),
            "topo": (sans, 12 if c else 18, "bold"),
            "mono": (mono, 8 if c else 10),
            "oled": (mono, 10 if c else 12),
            "grande": (sans, 14 if c else 22, "bold"),
        }
        self.pad = 6 if c else 12
        if c:
            self.geometry("800x480+0+0")
            self._maximizar()
        else:
            self.geometry("1100x720")
        self.minsize(640, 360)   # cabe mesmo com barra de tarefas e titulo
        self.tela_cheia = False
        self.bind("<F11>", lambda e: self.alternar_tela_cheia())
        self.bind("<Escape>", lambda e: self.alternar_tela_cheia(False))

        self.con = ConexaoSerial()
        self.cfg = carregar_pinos()
        self.pot = 0.0
        self.pot_suave = 0.0
        self.botao = 0
        self.hist_pot = deque([0] * HIST_PONTOS, maxlen=HIST_PONTOS)
        self.hist_som = deque([0] * HIST_PONTOS, maxlen=HIST_PONTOS)
        self.acel = (0.0, 0.0, 1.0)
        self.estado_leds = [0, 0, 0, 0]
        self.ultimo_nivel_bar = -1
        self.t_rgb = 0
        self.t_servo = 0
        self.assist = None          # estado do assistente de pinagem
        self.i2c_info = {"lista": "-", "sens": {}, "extras": []}

        self._estilo()
        self._montar_topo()
        self.abas = ttk.Notebook(self)
        self.abas.pack(fill="both", expand=True, padx=self.pad, pady=(0, self.pad))
        self._aba_saidas()
        self._aba_potenciometro()
        self._aba_sensores()
        self._aba_jogo()
        self._aba_pinagem()
        self._aba_terminal()

        self.atualizar_portas()
        self.after(30, self._ciclo)
        self.protocol("WM_DELETE_WINDOW", self._fechar)

    # -----------------------------------------------------------------
    # Janela
    # -----------------------------------------------------------------
    def _maximizar(self):
        for tentativa in (lambda: self.state("zoomed"),
                          lambda: self.attributes("-zoomed", True)):
            try:
                tentativa()
                return
            except tk.TclError:
                pass

    def alternar_tela_cheia(self, valor=None):
        self.tela_cheia = (not self.tela_cheia) if valor is None else valor
        self.attributes("-fullscreen", self.tela_cheia)

    # -----------------------------------------------------------------
    # Visual
    # -----------------------------------------------------------------
    def _estilo(self):
        F = self.F
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=COR_FUNDO, foreground=COR_TEXTO,
                     fieldbackground=COR_PAINEL, bordercolor=COR_BORDA, font=F["base"])
        st.configure("TNotebook", background=COR_FUNDO, borderwidth=0)
        st.configure("TNotebook.Tab", background=COR_PAINEL, foreground=COR_SUAVE,
                     padding=(10 if self.compacto else 16, 4), font=F["negrito"])
        st.map("TNotebook.Tab", background=[("selected", COR_DESTAQUE)],
               foreground=[("selected", "#111")])
        st.configure("Card.TFrame", background=COR_PAINEL)
        st.configure("Card.TLabel", background=COR_PAINEL, foreground=COR_TEXTO)
        st.configure("Titulo.TLabel", background=COR_PAINEL, foreground=COR_DESTAQUE, font=F["titulo"])
        st.configure("Valor.TLabel", background=COR_PAINEL, foreground=COR_TEXTO, font=F["valor"])
        st.configure("Suave.TLabel", background=COR_PAINEL, foreground=COR_SUAVE)
        st.configure("TButton", background=COR_BORDA, foreground=COR_TEXTO,
                     padding=(8, 3) if self.compacto else (10, 5), borderwidth=0)
        st.map("TButton", background=[("active", "#3a3f4c"), ("disabled", COR_PAINEL)],
               foreground=[("disabled", COR_SUAVE)])
        st.configure("Destaque.TButton", background=COR_DESTAQUE, foreground="#111", font=F["negrito"])
        st.map("Destaque.TButton", background=[("active", "#ffe066")])
        st.configure("Horizontal.TScale", background=COR_PAINEL, troughcolor=COR_BORDA)
        st.configure("TCheckbutton", background=COR_PAINEL, foreground=COR_TEXTO)
        st.map("TCheckbutton", background=[("active", COR_PAINEL)])
        st.configure("Horizontal.TProgressbar", background=COR_DESTAQUE, troughcolor=COR_BORDA)
        st.configure("TCombobox", arrowsize=14, padding=2)
        st.map("TCombobox", fieldbackground=[("readonly", COR_BORDA)],
               foreground=[("readonly", COR_TEXTO)], background=[("readonly", COR_BORDA)],
               selectbackground=[("readonly", COR_BORDA)], selectforeground=[("readonly", COR_TEXTO)])
        self.option_add("*TCombobox*Listbox.background", COR_PAINEL)
        self.option_add("*TCombobox*Listbox.foreground", COR_TEXTO)
        self.option_add("*TCombobox*Listbox.selectBackground", COR_DESTAQUE)
        self.option_add("*TCombobox*Listbox.selectForeground", "#111")
        self.option_add("*TCombobox*Listbox.font", F["titulo"])

    def _card(self, pai, titulo):
        f = ttk.Frame(pai, style="Card.TFrame", padding=self.pad)
        ttk.Label(f, text=titulo, style="Titulo.TLabel").pack(anchor="w", pady=(0, 4))
        return f

    def _aba(self, nome):
        aba = tk.Frame(self.abas, bg=COR_FUNDO)
        self.abas.add(aba, text=nome)
        return aba

    def _colunas(self, aba, n):
        for i in range(n):
            aba.columnconfigure(i, weight=1, uniform="col")
        aba.rowconfigure(0, weight=1)
        cols = []
        for i in range(n):
            f = tk.Frame(aba, bg=COR_FUNDO)
            f.grid(row=0, column=i, sticky="nsew",
                   padx=(0 if i == 0 else self.pad // 2, 0 if i == n - 1 else self.pad // 2),
                   pady=(self.pad, 0))
            cols.append(f)
        return cols

    # -----------------------------------------------------------------
    # Barra de conexao
    # -----------------------------------------------------------------
    def _montar_topo(self):
        topo = tk.Frame(self, bg=COR_FUNDO)
        topo.pack(fill="x", padx=self.pad, pady=self.pad)
        tk.Label(topo, text="PAINEL WISDOM", bg=COR_FUNDO, fg=COR_DESTAQUE,
                 font=self.F["topo"]).pack(side="left")
        ttk.Button(topo, text="Tela cheia", command=self.alternar_tela_cheia).pack(side="right", padx=(6, 0))
        self.lbl_status = tk.Label(topo, text="desconectado", bg=COR_FUNDO, fg=COR_ERRO,
                                   font=self.F["negrito"])
        self.lbl_status.pack(side="right", padx=(6, 0))
        self.btn_conectar = ttk.Button(topo, text="Conectar", style="Destaque.TButton",
                                       command=self.alternar_conexao)
        self.btn_conectar.pack(side="right", padx=4)
        ttk.Button(topo, text="Portas", command=self.atualizar_portas).pack(side="right")
        self.cmb_porta = ttk.Combobox(topo, width=22 if self.compacto else 38, state="readonly")
        self.cmb_porta.pack(side="right", padx=6)

    def atualizar_portas(self):
        portas = ConexaoSerial.listar()
        itens = ["%s  (%s)" % (d, desc) for d, desc in portas]
        self.cmb_porta["values"] = itens
        if itens:
            pref = [i for i, (_, desc) in enumerate(portas) if "CP210" in desc or "Silicon" in desc]
            self.cmb_porta.current(pref[0] if pref else 0)
        else:
            self.cmb_porta.set("nenhuma porta")

    def alternar_conexao(self):
        if serial is None:
            self.log("ERRO: pyserial nao instalado. Thonny > Ferramentas > Gerenciar pacotes > pyserial")
            self.abas.select(self.aba_terminal)
            return
        if self.con.conectado:
            self.parar_assistente()
            self.con.desconectar()
            self._status(False)
            return
        escolha = self.cmb_porta.get().split("  ")[0]
        if not escolha or escolha.startswith("nenhuma"):
            return
        try:
            self.con.conectar(escolha)
        except Exception as e:
            self.log("ERRO ao abrir %s: %s" % (escolha, e))
            self.log("Dica: feche o Thonny/Monitor Serial que estiver usando a placa.")
            self.abas.select(self.aba_terminal)
            return
        self._status(True)
        self.log("Conectado em " + escolha)
        self.after(1500, self._sincronizar)

    def _sincronizar(self):
        """Manda a pinagem salva e pede o diagnostico I2C."""
        self.enviar_pinagem()
        self.enviar("SCAN")
        self.after(300, lambda: self.enviar("OLED,PC conectado!|Bora programar"))

    def _status(self, ok):
        self.lbl_status.config(text="conectado" if ok else "desconectado",
                               fg=COR_OK if ok else COR_ERRO)
        self.btn_conectar.config(text="Desconectar" if ok else "Conectar")

    def enviar(self, comando):
        if self.con.enviar(comando):
            if self.var_eco.get():
                self.log("PC > " + comando)
            return True
        return False

    # -----------------------------------------------------------------
    # Aba: Saidas (LEDs, RGB, OLED, servo, rele)
    # -----------------------------------------------------------------
    def _aba_saidas(self):
        esq, dir_ = self._colunas(self._aba("Saidas"), 2)

        c = self._card(esq, "LEDs do Bargraph")
        c.pack(fill="x", pady=(0, self.pad))
        linha = tk.Frame(c, bg=COR_PAINEL)
        linha.pack(fill="x")
        self.btn_leds = []
        for i in range(4):
            b = tk.Button(linha, text="LED %d" % (i + 1), relief="flat", bg=COR_BORDA,
                          fg=COR_TEXTO, activebackground=COR_BORDA, font=self.F["negrito"],
                          command=lambda i=i: self.alternar_led(i))
            b.pack(side="left", expand=True, fill="x", padx=2, ipady=4 if self.compacto else 10)
            self.btn_leds.append(b)
        linha2 = tk.Frame(c, bg=COR_PAINEL)
        linha2.pack(fill="x", pady=(4, 0))
        for txt, cmd in (("Todos", lambda: self.todos_leds(1)), ("Nenhum", lambda: self.todos_leds(0)),
                         ("Sequencial", self.efeito_sequencial)):
            ttk.Button(linha2, text=txt, command=cmd).pack(side="left", expand=True, fill="x", padx=2)
        self.var_pot_bar = tk.BooleanVar(value=False)
        ttk.Checkbutton(c, text="Potenciometro controla o bargraph",
                        variable=self.var_pot_bar).pack(anchor="w", pady=(4, 0))

        c = self._card(esq, "LED RGB")
        c.pack(fill="both", expand=True)
        self.preview_rgb = tk.Canvas(c, height=14 if self.compacto else 30, bg="#000", highlightthickness=0)
        self.preview_rgb.pack(fill="x", pady=(0, 2))
        self.var_r, self.var_g, self.var_b = tk.IntVar(), tk.IntVar(), tk.IntVar()
        for nome, var in (("R", self.var_r), ("G", self.var_g), ("B", self.var_b)):
            f = tk.Frame(c, bg=COR_PAINEL)
            f.pack(fill="x")
            ttk.Label(f, text=nome, style="Card.TLabel", width=2).pack(side="left")
            ttk.Scale(f, from_=0, to=255, variable=var,
                      command=lambda _=None: self.rgb_mudou()).pack(side="left", fill="x", expand=True)
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x", pady=(4, 0))
        for cor in ("#ff0000", "#00ff00", "#0000ff", "#ffcc00", "#ff00ff", "#00ffff", "#ffffff", "#000000"):
            tk.Button(f, bg=cor, activebackground=cor, relief="flat", height=1,
                      command=lambda cor=cor: self.definir_cor(cor)).pack(side="left", padx=1, expand=True, fill="x")
        f2 = tk.Frame(c, bg=COR_PAINEL)
        f2.pack(fill="x", pady=(4, 0))
        ttk.Button(f2, text="Escolher cor", command=self.escolher_cor).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(f2, text="Arco-iris", command=self.efeito_arco_iris).pack(side="left", expand=True, fill="x", padx=2)
        self.var_pot_rgb = tk.BooleanVar(value=False)
        ttk.Checkbutton(c, text="Potenciometro muda a cor",
                        variable=self.var_pot_rgb).pack(anchor="w", pady=(4, 0))

        c_servo = self._card(dir_, "Servo e Rele")
        c_servo.pack(side="bottom", fill="x")
        c = self._card(dir_, "Display OLED")
        c.pack(fill="both", expand=True, pady=(0, self.pad))
        self.txt_oled = tk.Text(c, height=3 if self.compacto else 6, bg="#000", fg="#fff",
                                insertbackground="#fff", font=self.F["oled"], relief="flat", wrap="char")
        self.txt_oled.pack(fill="both", expand=True)
        self.txt_oled.insert("1.0", "Ola, turma!\nTio Rafa na area")
        self.txt_oled.bind("<KeyRelease>", self._oled_digitando)
        ttk.Label(c, text="Ate 6 linhas. Acentos sao removidos.", style="Suave.TLabel").pack(anchor="w", pady=2)
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x")
        ttk.Button(f, text="Enviar ao OLED", style="Destaque.TButton",
                   command=self.enviar_oled).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(f, text="Limpar", command=lambda: self.enviar("OLEDCLR")).pack(side="left", expand=True, fill="x", padx=2)
        self.var_oled_vivo = tk.BooleanVar(value=False)
        ttk.Checkbutton(c, text="Enviar enquanto digito", variable=self.var_oled_vivo).pack(anchor="w", pady=(4, 0))

        c = c_servo
        self.var_servo = tk.IntVar(value=90)
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x")
        ttk.Label(f, text="Servo", style="Card.TLabel", width=6).pack(side="left")
        ttk.Scale(f, from_=0, to=180, variable=self.var_servo,
                  command=lambda _=None: self.servo_mudou()).pack(side="left", fill="x", expand=True)
        self.lbl_servo = ttk.Label(f, text="90", style="Card.TLabel", width=4)
        self.lbl_servo.pack(side="left")
        self.var_rele = tk.BooleanVar(value=False)
        ttk.Checkbutton(c, text="Rele ligado", variable=self.var_rele,
                        command=lambda: self.enviar("RELE,%d" % self.var_rele.get())).pack(anchor="w", pady=(4, 0))

    # -----------------------------------------------------------------
    # Aba: Potenciometro
    # -----------------------------------------------------------------
    def _aba_potenciometro(self):
        esq, dir_ = self._colunas(self._aba("Potenciometro"), 2)
        c = self._card(esq, "Potenciometro deslizante")
        c.pack(fill="both", expand=True)
        self.lbl_pot = ttk.Label(c, text="---", style="Valor.TLabel")
        self.lbl_pot.pack(anchor="w")
        self.cv_robo = tk.Canvas(c, height=60 if self.compacto else 120, bg="#0d0f14", highlightthickness=0)
        self.cv_robo.pack(fill="both", expand=True, pady=4)
        self.cv_gauge = tk.Canvas(c, height=60 if self.compacto else 140, bg=COR_PAINEL, highlightthickness=0)
        self.cv_gauge.pack(fill="both", expand=True)

        c = self._card(dir_, "Grafico ao vivo")
        c.pack(fill="both", expand=True)
        ttk.Label(c, text="amarelo: potenciometro   verde: som",
                  style="Suave.TLabel").pack(side="bottom", anchor="w", pady=(4, 0))
        self.cv_graf = tk.Canvas(c, height=60, bg="#0d0f14", highlightthickness=0)
        self.cv_graf.pack(fill="both", expand=True)

    # -----------------------------------------------------------------
    # Aba: Sensores
    # -----------------------------------------------------------------
    def _aba_sensores(self):
        c1, c2, c3 = self._colunas(self._aba("Sensores"), 3)

        c = self._card(c1, "Botao")
        c.pack(fill="x", pady=(0, self.pad))
        self.cv_botao = tk.Canvas(c, width=60, height=60, bg=COR_PAINEL, highlightthickness=0)
        self.cv_botao.pack()
        self.bola_botao = self.cv_botao.create_oval(6, 6, 54, 54, fill=COR_BORDA, outline="")

        c = self._card(c1, "Som")
        c.pack(fill="both", expand=True)
        self.lbl_som = ttk.Label(c, text="--", style="Valor.TLabel")
        self.lbl_som.pack(anchor="w")
        self.cv_som = tk.Canvas(c, height=20, bg=COR_BORDA, highlightthickness=0)
        self.cv_som.pack(fill="x", pady=4)

        c = self._card(c2, "Ambiente")
        c.pack(fill="both", expand=True)
        self.lbl_temp = self._mini_valor(c, "Temperatura")
        self.lbl_umid = self._mini_valor(c, "Umidade")
        self.lbl_luz = self._mini_valor(c, "Luz")
        self.lbl_prox = self._mini_valor(c, "Proximidade")

        c = self._card(c3, "Acelerometro")
        c.pack(fill="both", expand=True)
        self.lbl_acel = ttk.Label(c, text="x --  y --  z --", style="Suave.TLabel")
        self.lbl_acel.pack(side="bottom", anchor="w", pady=(4, 0))
        self.cv_nivel = tk.Canvas(c, height=80, bg="#0d0f14", highlightthickness=0)
        self.cv_nivel.pack(fill="both", expand=True)

    def _mini_valor(self, pai, nome):
        ttk.Label(pai, text=nome, style="Suave.TLabel").pack(anchor="w")
        lbl = ttk.Label(pai, text="--", style="Valor.TLabel")
        lbl.pack(anchor="w", pady=(0, 2))
        return lbl

    # -----------------------------------------------------------------
    # Aba: Jogo
    # -----------------------------------------------------------------
    def _aba_jogo(self):
        aba = self._aba("Jogo")
        topo = tk.Frame(aba, bg=COR_FUNDO)
        topo.pack(fill="x", pady=4)
        ttk.Button(topo, text="Iniciar", style="Destaque.TButton", command=self.jogo_iniciar).pack(side="right")
        self.lbl_placar = tk.Label(topo, text="Pontos: 0   Vidas: 3", bg=COR_FUNDO,
                                   fg=COR_DESTAQUE, font=self.F["titulo"])
        self.lbl_placar.pack(side="right", padx=12)
        tk.Label(topo, text="Potenciometro move a raquete. Botao da placa inicia.",
                 bg=COR_FUNDO, fg=COR_SUAVE, font=self.F["base"]).pack(side="left")
        self.cv_jogo = tk.Canvas(aba, height=100, bg="#0d0f14", highlightthickness=0)
        self.cv_jogo.pack(fill="both", expand=True)
        self.jogo = {"ativo": False, "pontos": 0, "vidas": 3, "bolas": [], "t": 0}

    # -----------------------------------------------------------------
    # Aba: Pinagem (tabela + assistente + diagnostico I2C)
    # -----------------------------------------------------------------
    def _aba_pinagem(self):
        esq, dir_ = self._colunas(self._aba("Pinagem"), 2)

        c = self._card(esq, "Pinos da placa")
        c.pack(fill="both", expand=True)
        grade = tk.Frame(c, bg=COR_PAINEL)
        grade.pack(fill="x")
        self.cmb_pinos = {}
        metade = (len(FUNCOES) + 1) // 2
        for i, (chave, nome, entrada_ok) in enumerate(FUNCOES):
            lin, col = i % metade, (i // metade) * 2
            ttk.Label(grade, text=nome, style="Card.TLabel").grid(row=lin, column=col, sticky="w", padx=(0, 4), pady=1)
            valores = ["--"] + [str(p) for p in SAIDAS + (SO_ENTRADA if entrada_ok else [])]
            cmb = ttk.Combobox(grade, values=valores, width=4, state="readonly")
            cmb.set(str(self.cfg[chave]) if self.cfg[chave] >= 0 else "--")
            cmb.grid(row=lin, column=col + 1, sticky="w", padx=(0, 10), pady=1)
            cmb.bind("<<ComboboxSelected>>", lambda e, k=chave: self.pino_escolhido(k))
            self.cmb_pinos[chave] = cmb

        self.var_opc = {}
        for chave, texto in (("LEDINV", "LEDs acendem em nivel baixo"),
                             ("RGBINV", "RGB anodo comum (cor invertida)"),
                             ("BTNLOW", "Botao liga o pino ao GND")):
            v = tk.BooleanVar(value=bool(self.cfg[chave]))
            ttk.Checkbutton(c, text=texto, variable=v,
                            command=lambda k=chave: self.opcao_mudou(k)).pack(anchor="w")
            self.var_opc[chave] = v
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x", pady=(2, 0))
        ttk.Label(f, text="Driver do OLED", style="Card.TLabel").pack(side="left")
        self.cmb_oled = ttk.Combobox(f, values=["SSD1306", "SH1106"], width=8, state="readonly")
        self.cmb_oled.set("SH1106" if self.cfg["OLEDSH"] else "SSD1306")
        self.cmb_oled.bind("<<ComboboxSelected>>", lambda e: self.opcao_mudou("OLEDSH"))
        self.cmb_oled.pack(side="left", padx=6)
        ttk.Button(f, text="Reenviar", command=self.enviar_pinagem).pack(side="right")
        ttk.Button(f, text="Padrao", command=self.restaurar_padrao).pack(side="right", padx=4)
        ttk.Label(c, text="Servo e rele: conectores IO25 / IO33 da placa.",
                  style="Suave.TLabel").pack(anchor="w", pady=(2, 0))

        c_i2c = self._card(dir_, "Diagnostico I2C (SDA 21 / SCL 22)")
        c_i2c.pack(side="bottom", fill="x")
        c = self._card(dir_, "Assistente de pinagem")
        c.pack(fill="both", expand=True, pady=(0, self.pad))
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x")
        self.btn_etapas = []
        for txt, cmd in (("1 Potenc.", self.assist_pot), ("2 Microfone", self.assist_som),
                         ("3 Botao", self.assist_botao), ("4 LEDs", self.assist_leds)):
            b = ttk.Button(f, text=txt, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=1)
            self.btn_etapas.append(b)
        self.lbl_assist = tk.Label(c, bg=COR_PAINEL, fg=COR_TEXTO, font=self.F["base"], justify="left",
                                   anchor="nw", wraplength=360 if self.compacto else 480,
                                   text="Faca as etapas em ordem: 1, 2, 3 e depois 4.\n"
                                        "Primeiro as entradas, depois os LEDs.")
        self.lbl_assist.pack(fill="both", expand=True, pady=2)
        self.barra_assist = ttk.Progressbar(c, maximum=100)
        self.barra_assist.pack(fill="x")
        self.frm_respostas = tk.Frame(c, bg=COR_PAINEL)
        self.frm_respostas.pack(fill="x", pady=(4, 0))

        c = c_i2c
        self.lbl_i2c = tk.Label(c, bg=COR_PAINEL, fg=COR_TEXTO, font=self.F["mono"], justify="left",
                                anchor="w", text="Conecte a placa para ver os sensores.")
        self.lbl_i2c.pack(fill="x")
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x", pady=(4, 0))
        ttk.Button(f, text="Escanear de novo", command=lambda: self.enviar("SCAN")).pack(side="left", expand=True, fill="x", padx=1)
        ttk.Button(f, text="Buscar outro barramento", command=self.buscar_i2c).pack(side="left", expand=True, fill="x", padx=1)

    def pino_escolhido(self, chave):
        txt = self.cmb_pinos[chave].get()
        self.definir_pino(chave, -1 if txt == "--" else int(txt))

    def definir_pino(self, chave, pino):
        # um GPIO so pode ter uma funcao: libera quem ja usava
        if pino >= 0:
            for k, _, _ in FUNCOES:
                if k != chave and self.cfg[k] == pino:
                    self.cfg[k] = -1
                    self.cmb_pinos[k].set("--")
                    self.enviar("CFG,%s,-1" % k)
        self.cfg[chave] = pino
        self.cmb_pinos[chave].set(str(pino) if pino >= 0 else "--")
        self.enviar("CFG,%s,%d" % (chave, pino))
        self.salvar_pinagem()

    def restaurar_padrao(self):
        """Volta para a pinagem padrao da Wisdom."""
        for chave, pino in PINAGEM_PADRAO.items():
            self.cfg[chave] = pino
            self.cmb_pinos[chave].set(str(pino) if pino >= 0 else "--")
        for chave, valor in OPCOES:
            self.cfg[chave] = valor
            if chave in self.var_opc:
                self.var_opc[chave].set(bool(valor))
        self.cmb_oled.set("SSD1306")
        self.salvar_pinagem()
        self.enviar_pinagem()
        self._assist_texto("Pinagem padrao da Wisdom restaurada.")

    def opcao_mudou(self, chave):
        if chave == "OLEDSH":
            self.cfg[chave] = 1 if self.cmb_oled.get() == "SH1106" else 0
        else:
            self.cfg[chave] = 1 if self.var_opc[chave].get() else 0
        self.enviar("CFG,%s,%d" % (chave, self.cfg[chave]))
        self.salvar_pinagem()

    def salvar_pinagem(self):
        try:
            with open(ARQ_PINOS, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except OSError as e:
            self.log("Nao consegui salvar %s: %s" % (ARQ_PINOS, e))

    def enviar_pinagem(self):
        for chave, _ in OPCOES:
            self.enviar("CFG,%s,%d" % (chave, self.cfg[chave]))
        for chave, _, _ in FUNCOES:
            self.enviar("CFG,%s,%d" % (chave, self.cfg[chave]))

    def _mostrar_i2c(self):
        linhas = ["Enderecos: " + self.i2c_info["lista"]]
        itens = ["%s %s" % (n, e) for n, e in self.i2c_info["sens"].items()]
        for i in range(0, len(itens), 2):          # dois por linha: cabe na tela de 7"
            linhas.append("   ".join(itens[i:i + 2]))
        for extra in self.i2c_info["extras"]:
            linhas.append(extra)
        self.lbl_i2c.config(text="\n".join(linhas))

    def buscar_i2c(self):
        self.i2c_info["extras"] = ["buscando... (ate 30 s)"]
        self._mostrar_i2c()
        self.enviar("I2CHUNT")

    # -----------------------------------------------------------------
    # Assistente de pinagem
    # -----------------------------------------------------------------
    def _assist_texto(self, texto, progresso=None):
        self.lbl_assist.config(text=texto)
        if progresso is not None:
            self.barra_assist["value"] = progresso

    def _limpar_respostas(self):
        for w in self.frm_respostas.winfo_children():
            w.destroy()

    def _assist_comecar(self, nome, comando, fases):
        """fases: lista de (texto, segundos). Pede `comando` repetidamente
        e guarda as respostas de cada fase em self.assist['dados'][fase]."""
        if not self.con.conectado:
            self._assist_texto("Conecte a placa primeiro.")
            return
        self.parar_assistente()
        self.assist = {"nome": nome, "cmd": comando, "fases": fases, "fase": 0,
                       "dados": [[] for _ in fases], "fim": time.time() + fases[0][1],
                       "inicio": time.time(), "esperando": False, "contagem": 3}
        self._limpar_respostas()
        ttk.Button(self.frm_respostas, text="Cancelar", command=self.parar_assistente).pack(side="right")
        self._assist_contagem()

    def _assist_contagem(self):
        a = self.assist
        if a is None:
            return
        if a["contagem"] > 0:
            self._assist_texto("Prepare-se...  %d\n\n%s" % (a["contagem"], a["fases"][0][0]), 0)
            a["contagem"] -= 1
            self.after(1000, self._assist_contagem)
            return
        a["fim"] = time.time() + a["fases"][0][1]
        a["inicio"] = time.time()
        self._assist_pedir()

    def _assist_pedir(self):
        a = self.assist
        if a is None or a["cmd"] is None:
            return
        agora = time.time()
        if agora >= a["fim"]:
            a["fase"] += 1
            if a["fase"] >= len(a["fases"]):
                self._assist_concluir()
                return
            a["fim"] = agora + a["fases"][a["fase"]][1]
        texto, dur = a["fases"][a["fase"]]
        total = sum(d for _, d in a["fases"])
        feito = sum(d for _, d in a["fases"][:a["fase"]]) + (dur - (a["fim"] - agora))
        self._assist_texto("%s\n\n(%.0f s)" % (texto, max(0, a["fim"] - agora)), 100 * feito / total)
        a["esperando"] = True
        self.enviar(a["cmd"])
        # se a placa nao responder, tenta de novo
        a["vigia"] = self.after(1500, self._assist_pedir)

    def _assist_resposta(self, tipo, dados):
        a = self.assist
        if a is None or not a.get("esperando"):
            return
        esperado = {"ADCALL": "ADCS", "ADCPP": "ADCPP", "INALL": "IN"}[a["cmd"]]
        if tipo != esperado:
            return
        a["esperando"] = False
        self.after_cancel(a["vigia"])
        valores = {int(k): v for k, v in dados.items() if v is not None}
        a["dados"][a["fase"]].append(valores)
        self.after(80, self._assist_pedir)

    def _assist_concluir(self):
        a = self.assist
        nome = a["nome"]
        self.barra_assist["value"] = 100
        if nome == "pot":
            self._concluir_pot(a["dados"][0])
        elif nome == "som":
            self._concluir_som(a["dados"][0], a["dados"][1])
        elif nome == "btn":
            self._concluir_botao(a["dados"][0], a["dados"][1])
        self.assist = None
        self._limpar_respostas()
        self.enviar_pinagem()      # leituras de teste mexem nos pinos: restaura

    def parar_assistente(self):
        a = self.assist
        if a is None:
            return
        if a.get("vigia"):
            try:
                self.after_cancel(a["vigia"])
            except tk.TclError:
                pass
        if a["nome"] == "leds":
            self.enviar("PISCAR,-1")
        self.assist = None
        self._limpar_respostas()
        self._assist_texto("Assistente parado.", 0)
        self.enviar_pinagem()

    # ---- etapa 1: potenciometro ----
    def assist_pot(self):
        self._assist_comecar("pot", "ADCALL", [
            ("Deslize o potenciometro de uma ponta a outra, varias vezes.", 6)])

    def _concluir_pot(self, amostras):
        faixas = {}
        for s in amostras:
            for p, v in s.items():
                mn, mx = faixas.get(p, (MAX_ADC, 0))
                faixas[p] = (min(mn, v), max(mx, v))
        ordem = sorted(faixas.items(), key=lambda kv: kv[1][1] - kv[1][0], reverse=True)
        if ordem and ordem[0][1][1] - ordem[0][1][0] > 20000:
            p = ordem[0][0]
            self.definir_pino("POT", p)
            self._assist_texto("Potenciometro encontrado no GPIO %d.\nAgora a etapa 2." % p)
        else:
            self._assist_texto("Nao achei o potenciometro. Tente de novo, deslizando bem "
                               "de ponta a ponta.\nMaior variacao: %s" % self._resumo(ordem))

    # ---- etapa 2: microfone ----
    def assist_som(self):
        self._assist_comecar("som", "ADCPP", [
            ("Silencio! Medindo o ruido do ambiente.", 3),
            ("Agora bata palmas, assopre ou fale perto da placa!", 4)])

    def _concluir_som(self, silencio, barulho):
        base = {}
        for s in silencio:
            for p, v in s.items():
                base.setdefault(p, []).append(v)
        base = {p: sum(v) / len(v) for p, v in base.items()}
        pico = {}
        for s in barulho:
            for p, v in s.items():
                pico[p] = max(pico.get(p, 0), v)
        ganho = {p: pico[p] - base.get(p, 0) for p in pico if p != self.cfg["POT"]}
        ordem = sorted(ganho.items(), key=lambda kv: kv[1], reverse=True)
        if ordem and ordem[0][1] > 2500:
            p = ordem[0][0]
            self.definir_pino("SOM", p)
            self._assist_texto("Microfone encontrado no GPIO %d.\nAgora a etapa 3." % p)
        else:
            self._assist_texto("Nao achei o microfone. Tente de novo fazendo mais barulho "
                               "bem perto da placa.")

    # ---- etapa 3: botao ----
    def assist_botao(self):
        self._assist_comecar("btn", "INALL", [
            ("NAO aperte o botao.", 2),
            ("Agora APERTE e SEGURE o botao da placa!", 4)])

    def _concluir_botao(self, solto, apertado):
        if not solto or not apertado:
            self._assist_texto("A placa nao respondeu. Confira a conexao.")
            return
        candidatos = []
        for p in solto[0]:
            valores_solto = [s.get(p) for s in solto]
            if len(set(valores_solto)) != 1:      # pino instavel: ignora
                continue
            v0 = valores_solto[0]
            mudou = sum(1 for s in apertado if s.get(p) is not None and s[p] != v0)
            if mudou >= 0.6 * len(apertado):
                candidatos.append((p, v0))
        if len(candidatos) == 1:
            p, v0 = candidatos[0]
            self.var_opc["BTNLOW"].set(v0 == 1)
            self.opcao_mudou("BTNLOW")
            self.definir_pino("BTN", p)
            self._assist_texto("Botao encontrado no GPIO %d (%s).\nAgora a etapa 4." % (
                p, "liga ao GND" if v0 == 1 else "liga ao 3,3 V"))
        elif candidatos:
            self._assist_texto("Mais de um pino mudou: %s.\nRepita segurando o botao "
                               "o tempo todo." % ", ".join(str(p) for p, _ in candidatos))
        else:
            self._assist_texto("Nao achei o botao. Repita e segure o botao ate o fim.")

    # ---- etapa 4: LEDs ----
    def assist_leds(self):
        if not self.con.conectado:
            self._assist_texto("Conecte a placa primeiro.")
            return
        self.parar_assistente()
        ocupados = {self.cfg[k] for k in ("POT", "SOM", "BTN", "SERVO", "RELE")}
        fila = [p for p in SAIDAS if p not in ocupados]
        for chave in ("LED0", "LED1", "LED2", "LED3", "RGBR", "RGBG", "RGBB"):
            self.definir_pino(chave, -1)       # vai descobrir tudo de novo
        self.assist = {"nome": "leds", "fila": fila, "i": 0, "cmd": None}
        self._limpar_respostas()
        grade = tk.Frame(self.frm_respostas, bg=COR_PAINEL)
        grade.pack(fill="x")
        botoes = [("LED 1", "LED0"), ("LED 2", "LED1"), ("LED 3", "LED2"), ("LED 4", "LED3"),
                  ("RGB verm.", "RGBR"), ("RGB verde", "RGBG"), ("RGB azul", "RGBB"), ("Nada", None)]
        for i, (txt, chave) in enumerate(botoes):
            b = ttk.Button(grade, text=txt, style="Destaque.TButton" if chave is None else "TButton",
                           command=lambda k=chave: self._resposta_led(k))
            b.grid(row=i // 4, column=i % 4, sticky="ew", padx=1, pady=1)
            grade.columnconfigure(i % 4, weight=1)
        ttk.Button(self.frm_respostas, text="Cancelar", command=self.parar_assistente).pack(side="right", pady=(2, 0))
        self._proximo_led()

    def _proximo_led(self):
        a = self.assist
        if a is None:
            return
        if a["i"] >= len(a["fila"]):
            self.enviar("PISCAR,-1")
            self.assist = None
            self._limpar_respostas()
            self.enviar_pinagem()
            achados = ["%s=%d" % (n, self.cfg[k]) for k, n, _ in FUNCOES[:7] if self.cfg[k] >= 0]
            self._assist_texto("Pronto! Encontrados: %s.\nTeste na aba Saidas. Se acenderem ao "
                               "contrario, marque as opcoes de nivel baixo / anodo comum."
                               % (", ".join(achados) or "nenhum"), 100)
            return
        p = a["fila"][a["i"]]
        self.enviar("PISCAR,%d" % p)
        self._assist_texto("GPIO %d esta piscando (%d de %d).\nO que esta piscando na placa?"
                           % (p, a["i"] + 1, len(a["fila"])), 100 * a["i"] / len(a["fila"]))

    def _resposta_led(self, chave):
        a = self.assist
        if a is None or a["nome"] != "leds":
            return
        p = a["fila"][a["i"]]
        self.enviar("PISCAR,-1")
        if chave:
            for k, _, _ in FUNCOES:            # um GPIO, uma funcao
                if k != chave and self.cfg[k] == p:
                    self.cfg[k] = -1
                    self.cmb_pinos[k].set("--")
            self.cfg[chave] = p                # aplica no fim, para nao atrapalhar o pisca
            self.cmb_pinos[chave].set(str(p))
            self.salvar_pinagem()
        a["i"] += 1
        self.after(150, self._proximo_led)

    @staticmethod
    def _resumo(ordem):
        return ", ".join("GPIO %d (%d)" % (p, f[1] - f[0]) for p, f in ordem[:3]) or "-"

    # -----------------------------------------------------------------
    # Aba: Terminal
    # -----------------------------------------------------------------
    def _aba_terminal(self):
        aba = self._aba("Terminal")
        self.aba_terminal = aba
        f2 = tk.Frame(aba, bg=COR_FUNDO)
        f2.pack(side="bottom", fill="x")
        f = tk.Frame(aba, bg=COR_FUNDO)
        f.pack(side="bottom", fill="x")
        self.txt_log = tk.Text(aba, bg="#0d0f14", fg=COR_TEXTO, font=self.F["mono"],
                               relief="flat", state="disabled", height=5)
        self.txt_log.pack(fill="both", expand=True, pady=(self.pad, 4))
        self.ent_cmd = ttk.Entry(f, font=self.F["mono"])
        self.ent_cmd.pack(side="left", fill="x", expand=True)
        self.ent_cmd.bind("<Return>", lambda e: self._enviar_terminal())
        ttk.Button(f, text="Enviar", command=self._enviar_terminal).pack(side="left", padx=4)
        self.var_eco = tk.BooleanVar(value=False)
        self.var_tele = tk.BooleanVar(value=False)
        for txt, var in (("Mostrar comandos", self.var_eco), ("Mostrar telemetria", self.var_tele)):
            tk.Checkbutton(f2, text=txt, variable=var, bg=COR_FUNDO, fg=COR_TEXTO, selectcolor=COR_PAINEL,
                           activebackground=COR_FUNDO, highlightthickness=0, bd=0,
                           font=self.F["base"]).pack(side="left", padx=(0, 10))

    def _enviar_terminal(self):
        cmd = self.ent_cmd.get().strip()
        if cmd:
            self.enviar(cmd)
            self.ent_cmd.delete(0, "end")

    def log(self, texto):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", time.strftime("%H:%M:%S  ") + texto + "\n")
        if int(self.txt_log.index("end-1c").split(".")[0]) > 600:
            self.txt_log.delete("1.0", "100.0")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    # -----------------------------------------------------------------
    # Saidas
    # -----------------------------------------------------------------
    def alternar_led(self, i, valor=None):
        self.estado_leds[i] = (1 - self.estado_leds[i]) if valor is None else valor
        v = self.estado_leds[i]
        self.btn_leds[i].config(bg=CORES_BARGRAPH[i] if v else COR_BORDA, fg="#111" if v else COR_TEXTO,
                                activebackground=CORES_BARGRAPH[i] if v else COR_BORDA)
        self.enviar("LED,%d,%d" % (i, v))

    def todos_leds(self, v):
        for i in range(4):
            self.alternar_led(i, v)

    def efeito_sequencial(self, passo=0):
        if passo >= 12:
            self.todos_leds(0)
            return
        for i in range(4):
            self.alternar_led(i, 1 if i == passo % 4 else 0)
        self.after(150, lambda: self.efeito_sequencial(passo + 1))

    def rgb_mudou(self, forcar=False):
        r, g, b = self.var_r.get(), self.var_g.get(), self.var_b.get()
        self.preview_rgb.config(bg="#%02x%02x%02x" % (r, g, b))
        agora = time.time()
        if forcar or agora - self.t_rgb > 0.05:   # nao entope a serial
            self.t_rgb = agora
            self.enviar("RGB,%d,%d,%d" % (r, g, b))

    def definir_cor(self, hexa):
        self.var_r.set(int(hexa[1:3], 16))
        self.var_g.set(int(hexa[3:5], 16))
        self.var_b.set(int(hexa[5:7], 16))
        self.rgb_mudou(forcar=True)

    def escolher_cor(self):
        cor = colorchooser.askcolor(title="Cor do LED RGB")
        if cor and cor[1]:
            self.definir_cor(cor[1])

    def efeito_arco_iris(self, passo=0):
        if passo > 72:
            self.definir_cor("#000000")
            return
        self.definir_cor("#%02x%02x%02x" % self._roda_cores(passo / 72))
        self.after(60, lambda: self.efeito_arco_iris(passo + 1))

    @staticmethod
    def _roda_cores(x):
        h = (x % 1.0) * 6
        i = int(h)
        f = h - i
        q, t = int(255 * (1 - f)), int(255 * f)
        return [(255, t, 0), (q, 255, 0), (0, 255, t), (0, q, 255), (t, 0, 255), (255, 0, q)][i % 6]

    def enviar_oled(self):
        self.enviar("OLED," + self.txt_oled.get("1.0", "end-1c").replace("\n", "|"))

    def _oled_digitando(self, _evento):
        if self.var_oled_vivo.get():
            if getattr(self, "_oled_agendado", None):
                self.after_cancel(self._oled_agendado)
            self._oled_agendado = self.after(250, self.enviar_oled)

    def servo_mudou(self):
        a = self.var_servo.get()
        self.lbl_servo.config(text=str(a))
        agora = time.time()
        if agora - self.t_servo > 0.05:
            self.t_servo = agora
            self.enviar("SERVO,%d" % a)

    # -----------------------------------------------------------------
    # Mensagens da placa
    # -----------------------------------------------------------------
    def _processar(self, linha):
        if not linha.startswith(">"):
            if linha:
                self.log("placa: " + linha)
            return
        tipo, _, resto = linha[1:].partition(" ")
        if tipo in ("F", "S"):
            if self.var_tele.get():
                self.log(linha)
            d = converter_campos(resto)
            (self._dados_rapidos if tipo == "F" else self._dados_lentos)(d)
        elif tipo in ("ADCS", "ADCPP", "IN"):
            self._assist_resposta(tipo, converter_campos(resto))
        elif tipo == "I2C":
            self.i2c_info["lista"] = resto
            self._mostrar_i2c()
        elif tipo == "SENS":
            self.i2c_info["sens"] = {k: v for k, v in (p.split("=", 1) for p in resto.split(";") if "=" in p)}
            self._mostrar_i2c()
            self.log("Sensores: " + resto)
        elif tipo == "I2CBUS":
            d = dict(p.split("=", 1) for p in resto.split(";"))
            nomes = [NOMES_I2C.get(int(e, 16), e) for e in d.get("end", "").split(",") if e]
            extra = "SDA %s / SCL %s: %s" % (d.get("sda"), d.get("scl"), ", ".join(nomes))
            self.i2c_info["extras"] = [x for x in self.i2c_info["extras"] if not x.startswith("buscando")] + [extra]
            self._mostrar_i2c()
            self.log("Barramento I2C extra: " + extra)
        elif tipo == "INFO":
            self.log(resto)
            if "pronto" in resto:              # placa reiniciou: reenvia a pinagem
                self.after(300, self.enviar_pinagem)
            if resto.startswith("busca I2C concluida"):
                self.i2c_info["extras"] = [x for x in self.i2c_info["extras"] if not x.startswith("buscando")]
                if not self.i2c_info["extras"]:
                    self.i2c_info["extras"] = ["nenhum barramento extra"]
                self._mostrar_i2c()
        elif tipo == "HW":
            self.log("ATENCAO: a placa esta com o firmware antigo (v1). Grave a versao 2.")
        elif tipo in ("CFG", "PISCAR", "GPIO", "PONG"):
            if self.var_eco.get():
                self.log(tipo + " " + resto)
        else:
            self.log(tipo + ": " + resto)

    def _dados_rapidos(self, d):
        if d.get("pot") is not None:
            self.pot = d["pot"] / MAX_ADC
            self.lbl_pot.config(text="%5d   %3d %%" % (d["pot"], round(self.pot * 100)))
            self.hist_pot.append(self.pot)
            self._pot_controla_saidas()
        if d.get("som") is not None:
            nivel = min(1.0, d["som"] / 20000)
            self.hist_som.append(nivel)
            self.lbl_som.config(text="%d" % d["som"])
            w = self.cv_som.winfo_width()
            self.cv_som.delete("all")
            cor = COR_OK if nivel < 0.5 else COR_DESTAQUE if nivel < 0.8 else COR_ERRO
            self.cv_som.create_rectangle(0, 0, w * nivel, 20, fill=cor, outline="")
        if d.get("btn") is not None:
            novo = int(d["btn"])
            self.cv_botao.itemconfig(self.bola_botao, fill=COR_DESTAQUE if novo else COR_BORDA)
            if novo and not self.botao and not self.jogo["ativo"] and self.assist is None:
                self.jogo_iniciar()
            self.botao = novo

    def _pot_controla_saidas(self):
        if self.var_pot_bar.get():
            nivel = round(self.pot * 4)
            if nivel != self.ultimo_nivel_bar:
                self.ultimo_nivel_bar = nivel
                for i in range(4):
                    self.alternar_led(i, 1 if i < nivel else 0)
        if self.var_pot_rgb.get():
            r, g, b = self._roda_cores(self.pot * 0.83)
            self.var_r.set(r)
            self.var_g.set(g)
            self.var_b.set(b)
            self.rgb_mudou()

    def _dados_lentos(self, d):
        def fmt(v, un, casas=1):
            return "--" if v is None else ("%." + str(casas) + "f%s") % (v, un)
        self.lbl_temp.config(text=fmt(d.get("t"), " C"))
        self.lbl_umid.config(text=fmt(d.get("h"), " %"))
        self.lbl_luz.config(text=fmt(d.get("luz"), "", 0))
        self.lbl_prox.config(text=fmt(d.get("prox"), "", 0))
        if d.get("ax") is not None:
            self.acel = (d["ax"], d["ay"], d["az"])
            self.lbl_acel.config(text="x %+.2f  y %+.2f  z %+.2f g" % self.acel)

    # -----------------------------------------------------------------
    # Desenhos
    # -----------------------------------------------------------------
    def _desenhar_robo(self):
        cv = self.cv_robo
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        chao = h - 14
        s = max(0.5, min(1.0, h / 120))     # escala o robo em telas pequenas
        for x in range(0, w, 40):
            cv.create_line(x, chao + 8, x + 20, chao + 8, fill=COR_BORDA, width=3)
        cv.create_line(0, chao, w, chao, fill=COR_BORDA, width=2)
        x = 35 * s + self.pot_suave * (w - 70 * s)
        cv.create_rectangle(x - 30 * s, chao - 45 * s, x + 30 * s, chao - 12 * s, fill=COR_DESTAQUE, outline="")
        cv.create_rectangle(x - 18 * s, chao - 62 * s, x + 18 * s, chao - 45 * s, fill=COR_DESTAQUE, outline="")
        cv.create_line(x, chao - 62 * s, x, chao - 74 * s, fill=COR_TEXTO, width=2)
        cv.create_oval(x - 4, chao - 78 * s - 4, x + 4, chao - 78 * s + 4,
                       fill=COR_ERRO if self.botao else COR_OK, outline="")
        for ox in (-8, 8):
            cv.create_oval(x + ox * s - 4 * s, chao - 58 * s, x + ox * s + 4 * s, chao - 50 * s, fill="#111", outline="")
        for rx in (x - 18 * s, x + 18 * s):
            cv.create_oval(rx - 12 * s, chao - 24 * s, rx + 12 * s, chao, fill="#333", outline=COR_TEXTO, width=2)
            a = math.radians(self.pot_suave * 360 + (0 if rx < x else 45))
            cv.create_line(rx, chao - 12 * s, rx + 10 * s * math.cos(a), chao - 12 * s + 10 * s * math.sin(a),
                           fill=COR_TEXTO, width=2)

    def _desenhar_gauge(self):
        cv = self.cv_gauge
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        cx, cy = w / 2, h - 12
        r = max(20, min(w / 2 - 20, h - 24))
        esp = 12 if self.compacto else 16
        cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=180, style="arc", outline=COR_BORDA, width=esp)
        cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=180, extent=-180 * self.pot_suave,
                      style="arc", outline=COR_DESTAQUE, width=esp)
        ang = math.radians(180 - 180 * self.pot_suave)
        cv.create_line(cx, cy, cx + (r - 16) * math.cos(ang), cy - (r - 16) * math.sin(ang),
                       fill=COR_TEXTO, width=4, capstyle="round")
        cv.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=COR_TEXTO, outline="")

    def _desenhar_grafico(self):
        cv = self.cv_graf
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        for i in range(1, 4):
            cv.create_line(0, h * i / 4, w, h * i / 4, fill="#1c1f27")
        for serie, cor in ((self.hist_som, COR_OK), (self.hist_pot, COR_DESTAQUE)):
            pts = []
            for i, v in enumerate(serie):
                pts += [i * w / (HIST_PONTOS - 1), h - 4 - v * (h - 8)]
            cv.create_line(*pts, fill=cor, width=2)

    def _desenhar_nivel(self):
        cv = self.cv_nivel
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        cx, cy, r = w / 2, h / 2, max(20, min(w, h) / 2 - 8)
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=COR_BORDA, width=2)
        cv.create_oval(cx - r / 2, cy - r / 2, cx + r / 2, cy + r / 2, outline=COR_BORDA)
        cv.create_line(cx - r, cy, cx + r, cy, fill=COR_BORDA)
        cv.create_line(cx, cy - r, cx, cy + r, fill=COR_BORDA)
        ax, ay, _ = self.acel
        rb = max(8, r / 7)
        bx = cx - max(-1, min(1, ax)) * (r - rb)
        by = cy + max(-1, min(1, ay)) * (r - rb)
        nivelado = abs(ax) < 0.05 and abs(ay) < 0.05
        cv.create_oval(bx - rb, by - rb, bx + rb, by + rb, fill=COR_OK if nivelado else COR_DESTAQUE, outline="")

    # -----------------------------------------------------------------
    # Jogo Pega-Bolinha
    # -----------------------------------------------------------------
    def jogo_iniciar(self):
        self.jogo.update(ativo=True, pontos=0, vidas=3, bolas=[], t=0)
        self._placar()
        self.enviar("OLED,PEGA-BOLINHA||Pontos: 0|Vidas: 3")

    def _placar(self):
        self.lbl_placar.config(text="Pontos: %d   Vidas: %d" % (self.jogo["pontos"], self.jogo["vidas"]))

    def _passo_jogo(self):
        cv, j = self.cv_jogo, self.jogo
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        larg = max(80, w // 8)
        px = self.pot_suave * (w - larg)
        cv.create_rectangle(px, h - 24, px + larg, h - 12, fill=COR_DESTAQUE, outline="")
        if not j["ativo"]:
            cv.create_text(w / 2, h / 2, fill=COR_TEXTO, font=self.F["grande"],
                           text="Aperte o botao da placa para jogar")
            return
        j["t"] += 1
        vel = (3 if self.compacto else 4) + j["pontos"] * 0.25
        if j["t"] % max(15, 45 - j["pontos"]) == 0:
            j["bolas"].append([random.uniform(15, w - 15), -12])
        mudou = False
        for bola in j["bolas"][:]:
            bola[1] += vel
            if h - 36 <= bola[1] <= h - 12 and px - 10 <= bola[0] <= px + larg + 10:
                j["bolas"].remove(bola)
                j["pontos"] += 1
                mudou = True
                i = j["pontos"] % 4
                self.enviar("LED,%d,1" % i)
                self.after(120, lambda i=i: self.enviar("LED,%d,0" % i))
            elif bola[1] > h:
                j["bolas"].remove(bola)
                j["vidas"] -= 1
                mudou = True
                self.enviar("RGB,255,0,0")
                self.after(200, lambda: self.enviar("RGB,0,0,0"))
            else:
                cv.create_oval(bola[0] - 10, bola[1] - 10, bola[0] + 10, bola[1] + 10, fill=COR_OK, outline="")
        if mudou:
            self._placar()
            if j["vidas"] <= 0:
                j["ativo"] = False
                self.enviar("OLED,FIM DE JOGO!||Pontos: %d||Aperte o botao" % j["pontos"])
            else:
                self.enviar("OLED,PEGA-BOLINHA||Pontos: %d|Vidas: %d" % (j["pontos"], j["vidas"]))

    # -----------------------------------------------------------------
    # Ciclo da interface (~33 quadros por segundo)
    # -----------------------------------------------------------------
    def _ciclo(self):
        try:
            while True:
                tipo, conteudo = self.con.fila.get_nowait()
                if tipo == "LINHA":
                    self._processar(conteudo)
                else:
                    self.log("ERRO na porta: " + conteudo)
                    self.parar_assistente()
                    self.con.desconectar()
                    self._status(False)
        except queue.Empty:
            pass

        self.pot_suave += (self.pot - self.pot_suave) * 0.25
        aba = self.abas.index(self.abas.select())
        # so desenha a aba visivel: economiza processamento no Raspberry Pi
        if aba == 1:
            self._desenhar_robo()
            self._desenhar_gauge()
            self._desenhar_grafico()
        elif aba == 2:
            self._desenhar_nivel()
        if aba == 3 or self.jogo["ativo"]:
            self._passo_jogo()
        self.after(30, self._ciclo)

    def _fechar(self):
        if self.con.conectado:
            self.parar_assistente()
            self.con.enviar("OLED,Ate a proxima!")
            time.sleep(0.1)
            self.con.desconectar()
        self.destroy()


if __name__ == "__main__":
    PainelWisdom().mainloop()
