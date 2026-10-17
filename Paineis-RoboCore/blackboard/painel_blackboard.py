# =====================================================================
#  Painel BlackBoard + Radar - INTERFACE NO COMPUTADOR
#  Python 3 + Tkinter + PySerial (Thonny: interpretador "Python 3 local")
#  Requer: pyserial  (Thonny: Ferramentas > Gerenciar pacotes > pyserial)
#
#  A placa (RoboCore BlackBoard UNO R3) precisa estar com o firmware
#  PainelBlackBoard.ino gravado pela Arduino IDE.
#  Feche o Monitor Serial da IDE antes de conectar: so um programa usa
#  a porta por vez.
#
#  Tio Rafa - @prof.tiorafa
# =====================================================================

import math
import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

# ---------------------------------------------------------------------
# Configuracao (deve bater com o PainelBlackBoard.ino)
# ---------------------------------------------------------------------
BAUD = 115200
PINOS = list(range(2, 14))                  # D2..D13 (D0/D1 sao a USB)
PINOS_PWM = (3, 5, 6, 11)                   # D9/D10 perdem PWM por causa do Servo
RESERVADOS = {7: "TRIG", 8: "ECHO", 9: "SERVO"}
HIST_PONTOS = 150

COR_FUNDO = "#14161c"
COR_PAINEL = "#1e2129"
COR_BORDA = "#2c303b"
COR_TEXTO = "#e8eaf0"
COR_SUAVE = "#8b90a0"
COR_DESTAQUE = "#ffcc00"
COR_OK = "#3ddc84"
COR_ERRO = "#ff5c5c"
CORES_ANALOG = ["#ffcc00", "#3ddc84", "#4fc3f7", "#ff8a65", "#ba68c8", "#f06292"]

RADAR_FUNDO = "#030a05"
RADAR_GRADE = "#0f5a2a"
RADAR_VERDE = "#39ff6a"
RADAR_ALVO = "#ff3b3b"

MODOS = {"Saida": "OUT", "Entrada": "IN", "Entrada pull-up": "PUP"}


def converter_campos(texto):
    """'ang=30;cm=45' -> {'ang': 30, 'cm': 45}"""
    dados = {}
    for par in texto.split(";"):
        if "=" in par:
            k, v = par.split("=", 1)
            try:
                dados[k] = int(v)
            except ValueError:
                dados[k] = v
    return dados


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
        # No UNO, abrir a porta reinicia a placa (normal). O painel espera
        # o firmware voltar antes de mandar comandos.
        self.porta = serial.Serial(nome, BAUD, timeout=0.1)
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
            self.porta.write((comando + "\n").encode("ascii", "ignore"))
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
class PainelBlackBoard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Painel BlackBoard - Tio Rafa")
        self.configure(bg=COR_FUNDO)
        self.compacto = self.winfo_screenheight() <= 600
        if self.compacto:                         # tela de 7" (800x480)
            # 1 pixel por ponto: todas as fontes ficam ~25% menores
            self.tk.call("tk", "scaling", 1.0)
            self.geometry("800x480+0+0")
            for maximizar in (lambda: self.state("zoomed"), lambda: self.attributes("-zoomed", True)):
                try:
                    maximizar()
                    break
                except tk.TclError:
                    pass
        else:
            self.geometry("1180x800")
        self.minsize(640, 360)   # cabe mesmo com barra de tarefas e titulo
        self.tela_cheia = False
        self.bind("<F11>", lambda e: self._tela_cheia())
        self.bind("<Escape>", lambda e: self._tela_cheia(False))

        self.con = ConexaoSerial()
        self.saidas = {p: 0 for p in PINOS}
        self.modos = {p: "IN" for p in PINOS}
        self.niveis = {p: 0 for p in PINOS}
        self.analog = [0] * 6
        self.hist = [deque([0] * HIST_PONTOS, maxlen=HIST_PONTOS) for _ in range(6)]
        self.ultimo_pwm = {}

        # radar: angulo -> (cm, instante da leitura)
        self.leituras = {}
        self.ang_radar = 90
        self.cm_radar = -1
        self.modo_radar = "parado"
        self.dir_radar = 1

        self._estilo()
        self._montar_topo()
        abas = ttk.Notebook(self)
        m = 6 if self.compacto else 10
        abas.pack(fill="both", expand=True, padx=m, pady=(0, m))
        self._aba_pinos(abas)
        self._aba_analogicos(abas)
        self._aba_radar(abas)
        self._aba_terminal(abas)

        self.atualizar_portas()
        self.after(30, self._ciclo)
        self.protocol("WM_DELETE_WINDOW", self._fechar)

    def _tela_cheia(self, valor=None):
        self.tela_cheia = (not self.tela_cheia) if valor is None else valor
        self.attributes("-fullscreen", self.tela_cheia)

    # -----------------------------------------------------------------
    # Visual
    # -----------------------------------------------------------------
    def _estilo(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=COR_FUNDO, foreground=COR_TEXTO,
                     fieldbackground=COR_PAINEL, bordercolor=COR_BORDA,
                     font=("Segoe UI", 10))
        st.configure("TNotebook", background=COR_FUNDO, borderwidth=0)
        st.configure("TNotebook.Tab", background=COR_PAINEL, foreground=COR_SUAVE,
                     padding=(16, 6), font=("Segoe UI", 10, "bold"))
        st.map("TNotebook.Tab", background=[("selected", COR_DESTAQUE)],
               foreground=[("selected", "#111")])
        st.configure("Card.TFrame", background=COR_PAINEL)
        st.configure("Card.TLabel", background=COR_PAINEL, foreground=COR_TEXTO)
        st.configure("Titulo.TLabel", background=COR_PAINEL, foreground=COR_DESTAQUE,
                     font=("Segoe UI", 11, "bold"))
        st.configure("Valor.TLabel", background=COR_PAINEL, foreground=COR_TEXTO,
                     font=("Consolas", 20, "bold"))
        st.configure("Suave.TLabel", background=COR_PAINEL, foreground=COR_SUAVE)
        st.configure("TButton", background=COR_BORDA, foreground=COR_TEXTO,
                     padding=(10, 5), borderwidth=0)
        st.map("TButton", background=[("active", "#3a3f4c")])
        st.configure("Destaque.TButton", background=COR_DESTAQUE, foreground="#111",
                     font=("Segoe UI", 10, "bold"))
        st.map("Destaque.TButton", background=[("active", "#ffe066")])
        st.configure("Horizontal.TScale", background=COR_PAINEL, troughcolor=COR_BORDA)
        st.configure("TCheckbutton", background=COR_PAINEL, foreground=COR_TEXTO)
        st.map("TCheckbutton", background=[("active", COR_PAINEL)])
        st.configure("TSpinbox", arrowsize=12)
        # caixas de selecao legiveis no tema escuro
        for w in ("TCombobox", "TSpinbox"):
            st.map(w, fieldbackground=[("readonly", COR_BORDA), ("!disabled", COR_BORDA)],
                   foreground=[("readonly", COR_TEXTO), ("!disabled", COR_TEXTO)],
                   selectbackground=[("readonly", COR_BORDA)], selectforeground=[("readonly", COR_TEXTO)])
        self.option_add("*TCombobox*Listbox.background", COR_PAINEL)
        self.option_add("*TCombobox*Listbox.foreground", COR_TEXTO)
        self.option_add("*TCombobox*Listbox.selectBackground", COR_DESTAQUE)
        self.option_add("*TCombobox*Listbox.selectForeground", "#111")

    def _card(self, pai, titulo):
        f = ttk.Frame(pai, style="Card.TFrame", padding=6 if self.compacto else 12)
        ttk.Label(f, text=titulo, style="Titulo.TLabel").pack(anchor="w", pady=(0, 3 if self.compacto else 8))
        return f

    # -----------------------------------------------------------------
    # Barra de conexao
    # -----------------------------------------------------------------
    def _montar_topo(self):
        topo = tk.Frame(self, bg=COR_FUNDO)
        topo.pack(fill="x", padx=10, pady=10)
        tk.Label(topo, text="PAINEL BLACKBOARD", bg=COR_FUNDO, fg=COR_DESTAQUE,
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(topo, text="  RoboCore BlackBoard UNO R3", bg=COR_FUNDO,
                 fg=COR_SUAVE, font=("Segoe UI", 10)).pack(side="left")

        self.lbl_status = tk.Label(topo, text="desconectado", bg=COR_FUNDO,
                                   fg=COR_ERRO, font=("Segoe UI", 10, "bold"))
        self.lbl_status.pack(side="right", padx=(10, 0))
        self.btn_conectar = ttk.Button(topo, text="Conectar", style="Destaque.TButton",
                                       command=self.alternar_conexao)
        self.btn_conectar.pack(side="right", padx=4)
        ttk.Button(topo, text="Atualizar", command=self.atualizar_portas).pack(side="right")
        self.cmb_porta = ttk.Combobox(topo, width=38, state="readonly")
        self.cmb_porta.pack(side="right", padx=6)
        tk.Label(topo, text="Porta:", bg=COR_FUNDO, fg=COR_SUAVE).pack(side="right")

    def atualizar_portas(self):
        portas = ConexaoSerial.listar()
        itens = ["%s  (%s)" % (d, desc) for d, desc in portas]
        self.cmb_porta["values"] = itens
        if itens:
            pref = [i for i, (_, desc) in enumerate(portas)
                    if any(k in desc for k in ("CP210", "FT23", "FTDI", "CH340", "Arduino", "USB-SERIAL", "USB Serial"))]
            self.cmb_porta.current(pref[0] if pref else 0)
        else:
            self.cmb_porta.set("nenhuma porta encontrada")

    def alternar_conexao(self):
        if serial is None:
            self.log("ERRO: pyserial nao instalado. Thonny > Ferramentas > Gerenciar pacotes > pyserial")
            return
        if self.con.conectado:
            self.enviar("PARAR")
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
            self.log("Dica: feche o Monitor Serial da Arduino IDE.")
            return
        self._status(True)
        self.log("Conectado em %s. Aguardando a placa reiniciar..." % escolha)
        # o UNO reinicia ao abrir a porta; reaplica o que esta na tela
        self.after(2200, self._sincronizar)

    def _sincronizar(self):
        self.modo_radar = "parado"   # a placa reiniciou com o radar parado
        self.enviar("INFO")
        for p in PINOS:
            if p in RESERVADOS:
                continue
            if self.modos[p] != "IN":
                self.enviar("MODE,%d,%s" % (p, self.modos[p]))
            if self.modos[p] == "OUT" and self.saidas[p]:
                self.enviar("DW,%d,1" % p)

    def _status(self, ok):
        self.lbl_status.config(text="conectado" if ok else "desconectado",
                               fg=COR_OK if ok else COR_ERRO)
        self.btn_conectar.config(text="Desconectar" if ok else "Conectar")

    def enviar(self, comando):
        if self.con.enviar(comando):
            if self.var_eco.get():
                self.log("PC > " + comando)
        elif not self.con.conectado:
            self.log("(sem conexao) " + comando)

    # -----------------------------------------------------------------
    # Aba 1: pinos digitais
    # -----------------------------------------------------------------
    def _aba_pinos(self, abas):
        aba = tk.Frame(abas, bg=COR_FUNDO)
        abas.add(aba, text="  Pinos digitais  ")

        barra = tk.Frame(aba, bg=COR_FUNDO)
        barra.pack(fill="x", pady=8)
        ttk.Button(barra, text="Ligar todas as saidas", command=lambda: self.todas_saidas(1)).pack(side="left", padx=(0, 4))
        ttk.Button(barra, text="Desligar todas", command=lambda: self.todas_saidas(0)).pack(side="left", padx=4)
        ttk.Button(barra, text="Sequencial", command=self.efeito_sequencial).pack(side="left", padx=4)
        ttk.Button(barra, text="Pisca LED L (D13)", command=self.pisca_d13).pack(side="left", padx=4)
        tk.Label(barra, bg=COR_FUNDO, fg=COR_SUAVE,
                 text="D7, D8 e D9 ficam com o radar. PWM: D3, D5, D6, D11.").pack(side="right")

        grade = tk.Frame(aba, bg=COR_FUNDO)
        grade.pack(fill="both", expand=True)
        for c in range(6):
            grade.columnconfigure(c, weight=1, uniform="pino")
        grade.rowconfigure(0, weight=1)
        grade.rowconfigure(1, weight=1)

        self.wid = {}
        for i, p in enumerate(PINOS):
            card = ttk.Frame(grade, style="Card.TFrame", padding=10)
            card.grid(row=i // 6, column=i % 6, sticky="nsew", padx=4, pady=4)
            topo = tk.Frame(card, bg=COR_PAINEL)
            topo.pack(fill="x")
            tk.Label(topo, text="D%d" % p, bg=COR_PAINEL, fg=COR_DESTAQUE,
                     font=("Consolas", 18, "bold")).pack(side="left")
            if p in PINOS_PWM:
                tk.Label(topo, text="PWM", bg=COR_BORDA, fg=COR_TEXTO,
                         font=("Segoe UI", 8, "bold"), padx=4).pack(side="left", padx=6)
            if p == 13:
                tk.Label(topo, text="LED L", bg=COR_BORDA, fg=COR_TEXTO,
                         font=("Segoe UI", 8, "bold"), padx=4).pack(side="left", padx=2)
            nivel = tk.Canvas(topo, width=22, height=22, bg=COR_PAINEL, highlightthickness=0)
            nivel.pack(side="right")
            bola = nivel.create_oval(3, 3, 19, 19, fill=COR_BORDA, outline="")

            if p in RESERVADOS:
                tk.Label(card, text="Radar\n" + RESERVADOS[p], bg=COR_PAINEL, fg=COR_SUAVE,
                         font=("Segoe UI", 12, "bold"), justify="center").pack(expand=True)
                self.wid[p] = {"nivel": nivel, "bola": bola}
                continue

            var_modo = tk.StringVar(value="Entrada")
            cmb = ttk.Combobox(card, textvariable=var_modo, values=list(MODOS), state="readonly", width=14)
            cmb.pack(fill="x", pady=6)
            cmb.bind("<<ComboboxSelected>>", lambda e, p=p: self.mudar_modo(p))

            btn = tk.Button(card, text="DESLIGADO", relief="flat", bg=COR_BORDA, fg=COR_SUAVE,
                            font=("Segoe UI", 11, "bold"), state="disabled",
                            command=lambda p=p: self.alternar_saida(p))
            btn.pack(fill="x", ipady=8)

            var_pwm = None
            lbl_pwm = None
            if p in PINOS_PWM:
                var_pwm = tk.IntVar(value=0)
                f = tk.Frame(card, bg=COR_PAINEL)
                f.pack(fill="x", pady=(6, 0))
                ttk.Scale(f, from_=0, to=255, variable=var_pwm,
                          command=lambda _=None, p=p: self.pwm_mudou(p)).pack(side="left", fill="x", expand=True)
                lbl_pwm = tk.Label(f, text="0", width=4, bg=COR_PAINEL, fg=COR_TEXTO, font=("Consolas", 10))
                lbl_pwm.pack(side="left")

            self.wid[p] = {"nivel": nivel, "bola": bola, "modo": var_modo, "btn": btn,
                           "pwm": var_pwm, "lbl_pwm": lbl_pwm}

    def mudar_modo(self, p, modo_txt=None):
        w = self.wid[p]
        if modo_txt:
            w["modo"].set(modo_txt)
        codigo = MODOS[w["modo"].get()]
        self.modos[p] = codigo
        self.saidas[p] = 0
        self.enviar("MODE,%d,%s" % (p, codigo))
        self._pintar_botao(p)

    def _pintar_botao(self, p):
        w = self.wid[p]
        if self.modos[p] != "OUT":
            w["btn"].config(state="disabled", text="ENTRADA", bg=COR_BORDA, fg=COR_SUAVE)
            return
        lig = self.saidas[p]
        w["btn"].config(state="normal", text="LIGADO" if lig else "DESLIGADO",
                        bg=COR_DESTAQUE if lig else COR_BORDA,
                        fg="#111" if lig else COR_TEXTO,
                        activebackground=COR_DESTAQUE if lig else COR_BORDA)

    def alternar_saida(self, p, valor=None):
        if p in RESERVADOS:
            return
        if self.modos[p] != "OUT":
            self.mudar_modo(p, "Saida")
        self.saidas[p] = (1 - self.saidas[p]) if valor is None else valor
        if self.wid[p]["pwm"] is not None:
            self.wid[p]["pwm"].set(255 if self.saidas[p] else 0)
            self.wid[p]["lbl_pwm"].config(text=str(self.wid[p]["pwm"].get()))
        self.enviar("DW,%d,%d" % (p, self.saidas[p]))
        self._pintar_botao(p)

    def todas_saidas(self, v):
        for p in PINOS:
            if p not in RESERVADOS:
                self.alternar_saida(p, v)

    def efeito_sequencial(self, passo=0):
        livres = [p for p in PINOS if p not in RESERVADOS]
        if passo >= len(livres) * 2:
            self.todas_saidas(0)
            return
        for i, p in enumerate(livres):
            self.alternar_saida(p, 1 if i == passo % len(livres) else 0)
        self.after(120, lambda: self.efeito_sequencial(passo + 1))

    def pisca_d13(self, vezes=10):
        if vezes <= 0:
            self.alternar_saida(13, 0)
            return
        self.alternar_saida(13)
        self.after(200, lambda: self.pisca_d13(vezes - 1))

    def pwm_mudou(self, p):
        v = self.wid[p]["pwm"].get()
        self.wid[p]["lbl_pwm"].config(text=str(v))
        if self.modos[p] != "OUT":
            self.mudar_modo(p, "Saida")
        self.saidas[p] = 1 if v > 0 else 0
        self._pintar_botao(p)
        agora = time.time()
        if agora - self.ultimo_pwm.get(p, 0) > 0.05:   # nao entope a serial
            self.ultimo_pwm[p] = agora
            self.enviar("PWM,%d,%d" % (p, v))

    # -----------------------------------------------------------------
    # Aba 2: analogicos
    # -----------------------------------------------------------------
    def _aba_analogicos(self, abas):
        aba = tk.Frame(abas, bg=COR_FUNDO)
        abas.add(aba, text="  Entradas analogicas  ")
        esq = tk.Frame(aba, bg=COR_FUNDO)
        esq.pack(side="left", fill="y", pady=8)
        self.barras = []
        self.var_graf = []
        for i in range(6):
            c = ttk.Frame(esq, style="Card.TFrame", padding=(10, 4) if self.compacto else 10)
            c.pack(fill="x", pady=2)
            topo = tk.Frame(c, bg=COR_PAINEL)
            topo.pack(fill="x")
            tk.Label(topo, text="A%d" % i, bg=COR_PAINEL, fg=CORES_ANALOG[i],
                     font=("Consolas", 14, "bold")).pack(side="left")
            lbl = tk.Label(topo, text="----   -.-- V", bg=COR_PAINEL, fg=COR_TEXTO,
                           font=("Consolas", 12, "bold"), anchor="w")
            lbl.pack(side="left", padx=10)
            var = tk.BooleanVar(value=i < 2)
            ttk.Checkbutton(topo, text="no grafico", variable=var).pack(side="right")
            self.var_graf.append(var)
            cv = tk.Canvas(c, width=300, height=10, bg=COR_BORDA, highlightthickness=0)
            cv.pack(fill="x", pady=(2, 0))
            self.barras.append((lbl, cv))

        dir_ = self._card(aba, "Grafico ao vivo (0 a 1023)")
        dir_.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
        ttk.Label(dir_, style="Suave.TLabel", wraplength=380, justify="left", text=(
            "Dica: ligue um potenciometro (pontas no 5V e GND, meio no A0) "
            "ou um LDR com resistor de 10 k para ver o grafico se mexer.")).pack(side="bottom", anchor="w", pady=(6, 0))
        self.cv_graf = tk.Canvas(dir_, height=80, bg="#0d0f14", highlightthickness=0)
        self.cv_graf.pack(fill="both", expand=True)

    # -----------------------------------------------------------------
    # Aba 3: radar
    # -----------------------------------------------------------------
    def _aba_radar(self, abas):
        aba = tk.Frame(abas, bg=COR_FUNDO)
        abas.add(aba, text="  Radar ultrassonico  ")
        self.cv_radar = tk.Canvas(aba, bg=RADAR_FUNDO, highlightthickness=0)
        self.cv_radar.pack(side="left", fill="both", expand=True, pady=8)

        lado = tk.Frame(aba, bg=COR_FUNDO, width=280)
        lado.pack(side="left", fill="y", padx=(10, 0), pady=8)
        esp = 4 if self.compacto else 8

        # leitura + botoes principais no topo: sempre visiveis
        c = self._card(lado, "Leitura")
        c.pack(fill="x", pady=(0, esp))
        self.lbl_r_ang = ttk.Label(c, text="--", style="Valor.TLabel")
        self.lbl_r_ang.pack(anchor="w")
        self.lbl_r_perto = ttk.Label(c, text="Mais perto: --", style="Suave.TLabel")
        self.lbl_r_perto.pack(anchor="w", pady=(2, 0))
        f = tk.Frame(c, bg=COR_PAINEL)
        f.pack(fill="x", pady=(6, 0))
        ttk.Button(f, text="Iniciar", style="Destaque.TButton",
                   command=self.radar_iniciar).pack(side="left", expand=True, fill="x", padx=(0, 2))
        ttk.Button(f, text="Parar", command=self.radar_parar).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(f, text="Limpar", command=self.leituras.clear).pack(side="left", expand=True, fill="x", padx=(2, 0))

        c = self._card(lado, "Mira manual (arraste)")
        c.pack(fill="x", pady=(0, esp))
        self.var_mira = tk.IntVar(value=90)
        ttk.Scale(c, from_=180, to=0, variable=self.var_mira,
                  command=lambda _=None: self.radar_mirar()).pack(fill="x")
        self.t_mira = 0

        # ajustes em grade de duas colunas
        c = self._card(lado, "Ajustes")
        c.pack(fill="x")
        grade = tk.Frame(c, bg=COR_PAINEL)
        grade.pack(fill="x")
        self.var_amin = tk.IntVar(value=15)
        self.var_amax = tk.IntVar(value=165)
        self.var_passo = tk.IntVar(value=2)
        self.var_vel = tk.IntVar(value=50)
        self.var_alcance = tk.IntVar(value=100)
        self.var_alerta = tk.IntVar(value=20)
        self.var_rastro = tk.DoubleVar(value=4.0)
        ajustes = (("Inicio (graus)", self.var_amin, 0, 180, 5),
                   ("Fim (graus)", self.var_amax, 0, 180, 5),
                   ("Passo (graus)", self.var_passo, 1, 30, 1),
                   ("Pausa (ms)", self.var_vel, 20, 500, 10),
                   ("Alcance (cm)", self.var_alcance, 20, 400, 10),
                   ("Alerta (cm)", self.var_alerta, 0, 200, 5),
                   ("Rastro (s)", self.var_rastro, 1, 20, 1))
        for i, (rot, var, a, b, inc) in enumerate(ajustes):
            lin, col = i // 2, (i % 2) * 2
            ttk.Label(grade, text=rot, style="Card.TLabel").grid(row=lin, column=col, sticky="w",
                                                                  padx=(0 if col == 0 else 8, 2), pady=1)
            ttk.Spinbox(grade, from_=a, to=b, increment=inc, textvariable=var,
                        width=4).grid(row=lin, column=col + 1, sticky="w", pady=1)

    def _ler_int(self, var, padrao):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return padrao

    def radar_iniciar(self):
        self.modo_radar = "varrendo"
        self.enviar("VARRER,%d,%d,%d,%d" % (
            self._ler_int(self.var_amin, 15), self._ler_int(self.var_amax, 165),
            self._ler_int(self.var_passo, 2), self._ler_int(self.var_vel, 50)))

    def radar_parar(self):
        self.modo_radar = "parado"
        self.enviar("PARAR")

    def radar_mirar(self):
        self.modo_radar = "manual"
        agora = time.time()
        if agora - self.t_mira > 0.05:
            self.t_mira = agora
            self.enviar("SERVO,%d" % self.var_mira.get())

    # -----------------------------------------------------------------
    # Aba 4: terminal
    # -----------------------------------------------------------------
    def _aba_terminal(self, abas):
        aba = tk.Frame(abas, bg=COR_FUNDO)
        abas.add(aba, text="  Terminal  ")
        dica = tk.Label(aba, bg=COR_FUNDO, fg=COR_SUAVE, anchor="w", text=(
            "Comandos: MODE,p,OUT|IN|PUP  DW,p,v  PWM,p,0-255  SERVO,a  "
            "VARRER,min,max,passo,ms  PARAR  DIST  INFO  PING"))
        dica.pack(side="bottom", fill="x", pady=4)
        f = tk.Frame(aba, bg=COR_FUNDO)
        f.pack(side="bottom", fill="x")
        self.txt_log = tk.Text(aba, bg="#0d0f14", fg=COR_TEXTO, font=("Consolas", 10),
                               relief="flat", state="disabled", height=5)
        self.txt_log.pack(fill="both", expand=True, pady=8)
        self.ent_cmd = ttk.Entry(f, font=("Consolas", 11))
        self.ent_cmd.pack(side="left", fill="x", expand=True)
        self.ent_cmd.bind("<Return>", lambda e: self._enviar_terminal())
        ttk.Button(f, text="Enviar", command=self._enviar_terminal).pack(side="left", padx=6)
        self.var_eco = tk.BooleanVar(value=False)
        self.var_tele = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Mostrar comandos", variable=self.var_eco).pack(side="left")
        ttk.Checkbutton(f, text="Mostrar telemetria", variable=self.var_tele).pack(side="left")

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
    # Mensagens vindas da placa
    # -----------------------------------------------------------------
    def _processar(self, linha):
        if not linha.startswith(">"):
            if linha:
                self.log("placa: " + linha)
            return
        tipo, _, resto = linha[1:].partition(" ")
        if tipo in ("A", "R") and self.var_tele.get():
            self.log(linha)
        if tipo == "A":
            d = converter_campos(resto)
            for i in range(6):
                v = d.get("a%d" % i)
                if isinstance(v, int):
                    self.analog[i] = v
                    self.hist[i].append(v)
            mapa = d.get("d")
            if isinstance(mapa, int):
                for p in PINOS:
                    self.niveis[p] = (mapa >> p) & 1
        elif tipo == "R":
            d = converter_campos(resto)
            ang, cm = d.get("ang"), d.get("cm")
            if isinstance(ang, int) and isinstance(cm, int):
                if ang != self.ang_radar:
                    self.dir_radar = 1 if ang > self.ang_radar else -1
                self.ang_radar = ang
                self.cm_radar = cm
                self.leituras[ang] = (cm, time.time())
        elif tipo == "HW":
            self.log("Placa pronta: " + resto)
        elif tipo not in ("A", "R", "MODE"):
            self.log(tipo + ": " + resto)

    # -----------------------------------------------------------------
    # Desenhos
    # -----------------------------------------------------------------
    def _atualizar_pinos(self):
        for p in PINOS:
            w = self.wid[p]
            if p in RESERVADOS:
                continue
            w["nivel"].itemconfig(w["bola"], fill=COR_OK if self.niveis[p] else COR_BORDA)

    def _atualizar_analogicos(self):
        for i, (lbl, cv) in enumerate(self.barras):
            v = self.analog[i]
            lbl.config(text="%4d   %.2f V" % (v, v * 5 / 1023))
            w = cv.winfo_width()
            cv.delete("all")
            cv.create_rectangle(0, 0, w * v / 1023, 14, fill=CORES_ANALOG[i], outline="")
        cv = self.cv_graf
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        for k in range(1, 5):
            y = h * k / 5
            cv.create_line(0, y, w, y, fill="#1c1f27")
            cv.create_text(4, y - 8, text=str(int(1023 * (1 - k / 5))), fill=COR_SUAVE,
                           anchor="w", font=("Consolas", 8))
        for i, serie in enumerate(self.hist):
            if not self.var_graf[i].get():
                continue
            pts = []
            for j, v in enumerate(serie):
                pts += [j * w / (HIST_PONTOS - 1), h - 4 - v / 1023 * (h - 8)]
            cv.create_line(*pts, fill=CORES_ANALOG[i], width=2)

    def _desenhar_radar(self):
        cv = self.cv_radar
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50:
            return
        cv.delete("all")
        cx, cy = w / 2, h - 40
        r = min(w / 2 - 30, h - 70)
        alcance = max(10, self._ler_int(self.var_alcance, 100))
        alerta = self._ler_int(self.var_alerta, 20)
        try:
            rastro = max(0.5, float(self.var_rastro.get()))
        except (tk.TclError, ValueError):
            rastro = 4.0

        def ponto(ang, dist):
            a = math.radians(ang)
            return cx + dist * math.cos(a), cy - dist * math.sin(a)

        # grade: arcos de distancia e linhas de angulo
        for k in range(1, 5):
            rr = r * k / 4
            cv.create_arc(cx - rr, cy - rr, cx + rr, cy + rr, start=0, extent=180,
                          style="arc", outline=RADAR_GRADE, width=2 if k == 4 else 1)
            cv.create_text(cx + rr - 2, cy + 12, text="%d cm" % (alcance * k / 4),
                           fill=RADAR_VERDE, font=("Consolas", 9), anchor="e")
        for ang in range(0, 181, 30):
            x, y = ponto(ang, r)
            cv.create_line(cx, cy, x, y, fill=RADAR_GRADE)
            tx, ty = ponto(ang, r + 16)
            cv.create_text(tx, ty, text="%d" % ang, fill=RADAR_VERDE, font=("Consolas", 9))

        # alvos com rastro que vai sumindo
        agora = time.time()
        mais_perto = None
        for ang, (cm, t) in list(self.leituras.items()):
            idade = agora - t
            if idade > rastro:
                del self.leituras[ang]
                continue
            if cm < 0 or cm > alcance:
                continue
            forca = 1 - idade / rastro
            perto = cm <= alerta
            base = (255, 59, 59) if perto else (255, 140, 40)
            cor = "#%02x%02x%02x" % tuple(int(c * forca + 3 * (1 - forca)) for c in base)
            x1, y1 = ponto(ang, r * cm / alcance)
            x2, y2 = ponto(ang, r)
            cv.create_line(x1, y1, x2, y2, fill=cor, width=4)
            cv.create_oval(x1 - 4, y1 - 4, x1 + 4, y1 + 4, fill=cor, outline="")
            if mais_perto is None or cm < mais_perto[1]:
                mais_perto = (ang, cm)

        # feixe atual
        x, y = ponto(self.ang_radar, r)
        cv.create_line(cx, cy, x, y, fill=RADAR_VERDE, width=3)
        for k in range(1, 6):   # brilho atras do feixe, no sentido do giro
            x, y = ponto(self.ang_radar - k * 2 * self.dir_radar, r)
            g = int(255 * (1 - k / 6))
            cv.create_line(cx, cy, x, y, fill="#00%02x22" % g, width=2)

        estado = {"varrendo": "VARRENDO", "manual": "MIRA MANUAL", "parado": "PARADO"}[self.modo_radar]
        cv.create_text(16, 16, text="RADAR  " + estado, fill=RADAR_VERDE,
                       font=("Consolas", 14, "bold"), anchor="nw")
        if mais_perto and mais_perto[1] <= alerta:
            cv.create_text(w - 16, 16, text="ALERTA: OBJETO A %d cm" % mais_perto[1],
                           fill=RADAR_ALVO, font=("Consolas", 14, "bold"), anchor="ne")

        dist = "%d cm" % self.cm_radar if self.cm_radar >= 0 else "sem eco"
        self.lbl_r_ang.config(text="%3d graus  %s" % (self.ang_radar, dist))
        self.lbl_r_perto.config(text="Mais perto: " + (
            "%d cm em %d graus" % (mais_perto[1], mais_perto[0]) if mais_perto else "--"))

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
                    self.con.desconectar()
                    self._status(False)
        except queue.Empty:
            pass
        self._atualizar_pinos()
        self._atualizar_analogicos()
        self._desenhar_radar()
        self.after(30, self._ciclo)

    def _fechar(self):
        if self.con.conectado:
            self.con.enviar("PARAR")
            time.sleep(0.1)
            self.con.desconectar()
        self.destroy()


if __name__ == "__main__":
    PainelBlackBoard().mainloop()
