# Painel Wisdom v2.1

Controle da RoboCore BlackBoard Wisdom por uma tela no computador ou no Raspberry Pi.

## Pinagem da BlackBoard Wisdom

Descoberta com o assistente numa placa real. A partir da v2.1 ela já vem como padrão, tanto no firmware quanto na tela.

| Componente | GPIO | Observação |
|---|---|---|
| LED 1 do bargraph | 13 | |
| LED 2 | 4 | |
| LED 3 | 16 | |
| LED 4 | 17 | |
| RGB vermelho | 19 | PWM |
| RGB verde | 23 | PWM |
| RGB azul | 18 | PWM |
| Botão | 27 | liga o pino ao GND (ativo em nível baixo) |
| Potenciômetro | 39 | ADC1, só entrada |
| Microfone | 36 | ADC1, só entrada |
| I2C (OLED, HTU21D, MMA8452Q, APDS-9960) | SDA 21 / SCL 22 | confirmado pela RoboCore |
| Servo / relé | IO25 / IO33 | não configurados por padrão |

O botão **Padrão**, na aba Pinagem, volta para essa tabela a qualquer momento.

## O que mudou na v2

- **Cabe na tela de 7" do Raspberry Pi (800x480)**, mesmo com a barra de tarefas e a barra de título aparecendo. **F11** liga e desliga a tela cheia, e **Esc** sai dela.
- **Não precisa mais de datasheet.** A aba **Pinagem** tem um assistente que descobre sozinho em qual GPIO está cada componente. A pinagem fica salva em `wisdom_pinos.json`, ao lado do `painel_wisdom.py`, e é enviada para a placa toda vez que você conecta. Não precisa regravar o firmware para trocar pino.
- **Diagnóstico I2C:** mostra quais sensores responderam, quais falharam e por quê. Também procura se algum sensor está ligado em outros pinos.
- **Inicialização dos sensores melhorada:** o APDS-9960 recebeu tempo de integração, pulsos de IR e ganho ajustados. O MMA8452Q agora confere a identificação do chip antes de ser usado.
- O driver do OLED (SSD1306 ou SH1106) é trocado pela tela, sem regravar a placa.

## Arquivos

| Arquivo | Onde roda |
|---|---|
| `painel_wisdom.py` | computador ou Raspberry Pi (Python 3 + Tkinter + PySerial) |
| `arduino/PainelWisdom/PainelWisdom.ino` | placa, gravado pela Arduino IDE |
| `micropython/main.py` | placa, gravado pelo Thonny |

Grave **um** dos dois firmwares. Os dois falam o mesmo protocolo e funcionam com a mesma tela.

## 1. Gravar o firmware (escolha um)

**Arduino IDE**

1. Instale o pacote "esp32 by Espressif Systems", versão 2.x ou 3.x.
2. Instale a biblioteca **U8g2** (de oliver).
3. Abra `arduino/PainelWisdom/PainelWisdom.ino`.
4. Escolha a placa **ESP32 Dev Module** e a porta.
5. Clique em Carregar.

**MicroPython (Thonny)**

1. No canto inferior direito do Thonny, escolha *MicroPython (ESP32)*.
2. Abra `micropython/main.py`.
3. Vá em Arquivo > Salvar como > Dispositivo MicroPython > `main.py`.
4. Aperte o reset da placa.

Nos dois casos não é preciso editar nada no firmware.

## 2. Abrir a tela

**No Raspberry Pi**

```
sudo apt install python3-tk python3-serial
sudo usermod -aG dialout $USER     # depois saia e entre de novo na sessao
python3 painel_wisdom.py
```

A porta da Wisdom costuma aparecer como `/dev/ttyUSB0 (CP2102...)`.

**No Windows ou no Thonny**

1. Instale o `pyserial` (no Thonny: Ferramentas > Gerenciar pacotes).
2. Rode `painel_wisdom.py` com o interpretador *Python 3 local*.

Depois, escolha a porta e clique em **Conectar**.

## 3. Descobrir a pinagem (só se for outra placa ou outra revisão)

A pinagem da Wisdom já vem pronta. Use o assistente se uma placa de outra revisão não acender os LEDs certos. Vá na aba **Pinagem** e faça as etapas **na ordem**:

1. **Potenciômetro:** fique deslizando o potenciômetro de ponta a ponta durante 6 s.
2. **Microfone:** fique em silêncio por 3 s. Depois bata palmas, assopre ou fale perto da placa por 4 s.
3. **Botão:** não aperte nada por 2 s. Depois aperte e **segure** o botão por 4 s.
4. **LEDs:** a tela faz cada GPIO piscar, um por vez. Para cada um, você clica no que está piscando: LED 1 a 4 (da esquerda para a direita), RGB vermelho, verde ou azul, ou **Nada**.

A ordem importa. Primeiro a tela descobre as entradas, e só depois faz os pinos piscarem como saída. Assim nenhuma saída fica forçando um pino de entrada.

Para conferir o resultado:

- **Bargraph:** vá na aba **Saídas** e teste os LEDs. Se eles acenderem ao contrário (apagados quando a tela diz ligado), marque "LEDs acendem em nível baixo".
- **RGB:** se a cor sair invertida, por exemplo azul-claro quando deveria ser vermelho, marque "RGB ânodo comum".
- **Servo e relé:** ficam nos conectores IO25 e IO33. Escolha na tabela conforme a serigrafia da placa.

Você também pode escolher qualquer pino direto na tabela. A mudança vale na hora e fica salva.

## 4. Sensores I2C: como ler o diagnóstico

O quadro "Diagnóstico I2C", na aba Pinagem, mostra uma linha para cada sensor:

| Aparece | Significa | O que fazer |
|---|---|---|
| `ok` | respondeu e foi iniciado | nada |
| `ausente` | não respondeu em SDA 21 / SCL 22 | clique em **Buscar outro barramento**: a placa testa outros pares de pinos com resistor de pull-up |
| `falhou: WHO_AM_I=...` ou `ID=...` | algo respondeu no endereço, mas não é o chip esperado | copie a linha e me mande |
| OLED `ok`, mas a tela embaralhada | o driver está errado | troque o driver do OLED para SH1106 |

Se a busca encontrar sensores em outros pinos, a linha aparece como `SDA x / SCL y: ...`. **Copie o quadro inteiro e me mande.** Com isso eu ajusto o firmware para ler o barramento certo.

## 5. Protocolo (para quem quiser estudar)

| Do PC para a placa | Efeito |
|---|---|
| `CFG,chave,pino` | define a função de um GPIO: `LED0` a `LED3`, `RGBR`, `RGBG`, `RGBB`, `BTN`, `POT`, `SOM`, `SERVO`, `RELE` (`-1` desliga) |
| `CFG,opcao,0/1` | `LEDINV`, `RGBINV`, `BTNLOW`, `OLEDSH` |
| `LED,i,v` · `RGB,r,g,b` · `OLED,texto` · `OLEDCLR` · `SERVO,a` · `RELE,v` | saídas |
| `PISCAR,n` / `PISCAR,-1` | pisca um GPIO (assistente de LEDs) |
| `ADCALL` · `ADCPP` · `INALL` | leituras de todos os pinos (assistente de entradas) |
| `SCAN` · `I2CHUNT` | diagnóstico I2C |
| `GPIO,n,v` · `PING` | teste |

A placa responde com linhas que começam por `>`:

- `>F` a cada 100 ms: potenciômetro, botão e som.
- `>S` a cada 1,5 s: ambiente e acelerômetro.
- `>I2C`, `>SENS`, `>I2CBUS`, `>ADCS`, `>ADCPP`, `>IN`, `>CFG`, `>INFO`, `>ERR`.

## 6. Problemas comuns

- **A janela não cabe:** use **F11** para a tela cheia. A janela também pode ser redimensionada até 640x360.
- **"Acesso negado" ao conectar:** o Thonny ou o Monitor Serial ainda está usando a porta.
- **No Pi, "Permission denied" em /dev/ttyUSB0:** falta o comando `usermod -aG dialout`, ou você ainda não saiu e entrou de novo na sessão.
- **Terminal mostra "firmware antigo (v1)":** grave um dos firmwares desta pasta.

## 7. Validação

- **Firmware Arduino:** compilado com os cores ESP32 **3.3.2** e **2.0.17** reais (ESP32 Dev Module), sem nenhum aviso com `-Wall -Wextra`. Também foi simulado: comandos CFG, troca de pino, LEDs invertidos, troca de driver do OLED, pisca, leituras de descoberta e busca I2C.
- **Firmware MicroPython:** simulado com hardware falso, cobrindo os mesmos comandos.
- **Tela:** testada com uma placa simulada no Tk 8.6 e com a fonte DejaVu, a mesma do Raspberry Pi OS. O assistente acertou potenciômetro, microfone, botão e LEDs, e ignorou os pinos que ficam oscilando. Todas as abas foram conferidas em 800x414 (tela de 7" com barras) e em tela grande.
- **Hardware real:** TESTADO EM HARDWARE numa BlackBoard Wisdom ligada a um Raspberry Pi com tela de 7". O assistente encontrou a pinagem da tabela acima, e LEDs, RGB, botão, potenciômetro, microfone, OLED, temperatura/umidade, luz/proximidade e acelerômetro funcionaram.
