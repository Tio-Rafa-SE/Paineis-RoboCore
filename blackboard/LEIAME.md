# Painel BlackBoard + Radar Ultrassônico

Controle dos pinos da RoboCore BlackBoard UNO R3 e radar com HC-SR04 sobre servo, numa tela no computador.

- `PainelBlackBoard/PainelBlackBoard.ino` vai **para a placa**, pela Arduino IDE.
- `painel_blackboard.py` roda **no computador** (Python 3 + Tkinter + PySerial). Pode ser aberto no Thonny, em "Python 3 local".

## 1. Entendimento do projeto

| Item | Valor |
|---|---|
| Placa | RoboCore BlackBoard UNO R3 |
| Microcontrolador | ATmega328P, 16 MHz, 5 V |
| USB-Serial | CP2102N (V2) ou FT231X (V1.1), conforme o tutorial de driver da RoboCore |
| Placa na IDE | **Arduino Uno** (BlackBoard é compatível com o UNO; a RoboCore não nomeia a opção na página de driver) |
| Bibliotecas | `Servo` (já vem com a Arduino IDE) |
| Comunicação | USB, 115200 baud, comandos em texto |

## 2. Montagem do radar

| Componente | Pino do componente | Liga em |
|---|---|---|
| HC-SR04 | VCC | 5V |
| HC-SR04 | GND | GND |
| HC-SR04 | TRIG | D7 |
| HC-SR04 | ECHO | D8 |
| Servo (SG90 ou similar) | marrom/preto | GND |
| Servo | vermelho | 5V |
| Servo | laranja/amarelo (sinal) | D9 |

Para trocar os pinos, altere `PINO_TRIG`, `PINO_ECHO` e `PINO_SERVO` no topo do `.ino` e a linha `RESERVADOS` no topo do `.py`.

**Sobre alimentação:** um SG90 sem carga funciona pela USB. Se a placa reiniciar quando o servo mexe, alimente o servo com uma fonte de 5 V separada e **ligue o GND da fonte no GND da placa**.

**Sobre PWM:** a biblioteca Servo usa o Timer1 do ATmega328P. Com o servo ligado, D9 e D10 perdem o `analogWrite`. Por isso a tela só oferece PWM em D3, D5, D6 e D11. O D10 continua funcionando como liga/desliga.

## 3. Como gravar

1. Instale o driver da BlackBoard, se a porta não aparecer: robocore.net/tutoriais/instalacao-driver-da-blackboard.
2. Abra `PainelBlackBoard/PainelBlackBoard.ino` na Arduino IDE.
3. Em Ferramentas > Placa, escolha **Arduino Uno**. Selecione a porta.
4. Clique em Carregar. O servo vai para 90 graus.
5. Se quiser conferir, abra o Monitor Serial em 115200. Devem aparecer `>INFO Painel BlackBoard pronto` e as linhas `>A`. **Feche o Monitor Serial antes de abrir a tela.**

## 4. Como abrir a tela

1. Instale o PySerial. No Thonny: Ferramentas > Gerenciar pacotes > `pyserial`. No terminal: `pip install pyserial`.
2. Rode `painel_blackboard.py`, escolha a porta e clique em **Conectar**.
3. A placa reinicia ao conectar, o que é normal no UNO. Em cerca de 2 segundos a tela reenvia o estado dos pinos.

A tela cabe no display de 7" do Raspberry Pi (800x480), mesmo com a barra de tarefas e a barra de título. **F11** liga e desliga a tela cheia, e **Esc** sai dela. No Pi, instale com `sudo apt install python3-tk python3-serial` e coloque seu usuário no grupo `dialout` (`sudo usermod -aG dialout $USER`).

### Abas

- **Pinos digitais:** um cartão para cada pino de D2 a D13. Em cada cartão você escolhe o modo (Saída, Entrada ou Entrada pull-up) e usa o botão LIGADO/DESLIGADO. A bolinha verde mostra o nível lido no pino, o que serve para botões e sensores digitais. Os pinos com PWM têm um slider de 0 a 255. Há também botões para ligar/desligar todas as saídas, fazer um efeito sequencial e piscar o LED L (D13). D7, D8 e D9 ficam travados para o radar.
- **Entradas analógicas:** A0 a A5 com valor bruto, tensão e barra, mais um gráfico ao vivo em que você escolhe quais entradas aparecem.
- **Radar ultrassônico:** uma tela de radar em semicírculo com arcos de distância e o feixe girando.
  - Os objetos deixam um rastro que vai sumindo.
  - Abaixo da distância de alerta, o objeto fica vermelho e aparece um aviso.
  - Os botões **Iniciar**, **Parar** e **Limpar** ficam no topo, junto com a leitura atual.
  - Em **Ajustes** você configura o ângulo inicial e final, o passo, a pausa por passo, o alcance da tela, a distância de alerta e o tempo do rastro.
  - Na mira manual, você arrasta o slider para apontar o sensor, e ele mede continuamente naquela direção.
- **Terminal:** mostra as mensagens da placa e permite enviar comandos na mão.

## 5. Protocolo

| Do PC para a placa | Efeito |
|---|---|
| `MODE,p,OUT` / `IN` / `PUP` | modo do pino p (2 a 13, menos os do radar) |
| `DW,p,v` | saída digital 0/1 (já coloca o pino como saída) |
| `PWM,p,v` | PWM 0 a 255 em D3, D5, D6, D11 |
| `SERVO,a` | mira manual em `a` graus e mede a cada 100 ms |
| `VARRER,min,max,passo,ms` | varredura indo e voltando |
| `PARAR` | para o radar |
| `DIST` | uma medição no ângulo atual |
| `INFO` / `PING` | diagnóstico |

A placa responde com linhas que começam por `>`:

- `>A a0=..;a1=..;...;a5=..;d=mapa` a cada 100 ms. O `mapa` traz o bit `p` igual a 1 quando o pino Dp está em nível alto.
- `>R ang=..;cm=..` a cada medição. `cm=-1` quer dizer sem eco, ou seja, fora de alcance.
- `>HW`, `>MODE`, `>INFO`, `>ERR`.

## 6. Teste

| Teste | Ação | Resultado esperado | Resultado real |
|---|---|---|---|
| 01 | Gravar e ligar | servo vai para 90 graus | |
| 02 | Conectar a tela | status "conectado"; terminal mostra "Placa pronta" | |
| 03 | D13: modo Saída, LIGADO | LED L da placa acende | |
| 04 | LED + resistor 220 R no D5, slider PWM | brilho varia | |
| 05 | Botão entre D2 e GND, modo Entrada pull-up | bolinha apaga ao apertar | |
| 06 | Potenciômetro no A0 | valor e gráfico acompanham | |
| 07 | Radar > Iniciar | servo varre; obstáculos aparecem na tela | |
| 08 | Mão a menos de 20 cm | alvo vermelho e aviso de alerta | |
| 09 | Mira manual | servo segue o slider; distância atualiza | |
| 10 | Radar > Parar | servo para | |

## 7. Problemas comuns

- **Porta não aparece**: instale o driver da BlackBoard (CP2102N ou FTDI, conforme a versão).
- **"Acesso negado" na tela**: feche o Monitor Serial da IDE.
- **Radar mostra tudo como "sem eco"**: confira TRIG e ECHO (se estiverem invertidos, nada funciona) e se o VCC está no 5V.
- **Leituras pulando**: aumente a "pausa por passo" para 80 a 100 ms, porque o eco anterior pode atrapalhar a próxima medição.
- **Placa reinicia com o servo**: use fonte separada para o servo, com GND comum.

## 8. Validação

- **Compilação:** compilado com o core AVR oficial (ArduinoCore-avr), a biblioteca Servo oficial e avr-gcc 14.1, para ATmega328P a 16 MHz. Não deu nenhum aviso com `-Wall -Wextra`. Usa 10.328 de 32.256 bytes de flash e 629 de 2.048 bytes de RAM.
- **Simulação do firmware:** comandos, pinos bloqueados (D0, D1 e os do radar), PWM só nos pinos válidos, varredura com reversão nos limites, mira manual e telemetria.
- **Tela Python:** renderizada com uma placa simulada em 800x414 (tela de 7" com barras) e em tela grande. Todas as abas couberam.
- **Hardware real:** a tela foi usada numa BlackBoard UNO R3 real, no Raspberry Pi com tela de 7" do cyberdeck.
