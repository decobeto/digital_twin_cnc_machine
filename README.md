# Digital Twin IIoT — Fresadora CNC (AI4I 2020 + Eclipse BaSyx + Three.js)

Arquitetura completa ponta a ponta de **Gêmeo Digital (Digital Twin)** para a Indústria 4.0, integrando o dataset de manutenção preditiva **AI4I 2020**, o middleware padronizado de **Asset Administration Shell (AAS) Eclipse BaSyx v2 (API v3.0)** e um painel 3D interativo em **Three.js + HUD Industrial**.

---

## 1. Arquitetura do Sistema

```
+-------------------------------------------------------------------------+
|                   CAMADA 1: SIMULADOR IIoT (Python)                     |
|  - Script: cnc_simulator.py + Dataset: ai4i2020.csv (10.000 ciclos)     |
|  - Provisiona o AAS/Submodelo e envia telemetria a 1 Hz via HTTP REST   |
+------------------------------------+------------------------------------+
                                     | PATCH /submodels/{id}/submodel-elements/{prop}/$value
                                     v
+-------------------------------------------------------------------------+
|               CAMADA 2: MIDDLEWARE ECLIPSE BASYX (Docker)               |
|  - AAS Server & Submodel Server (aas-environment)  -> Porta 8081        |
|  - AAS Registry (aas-registry-log-mem)             -> Porta 8082        |
|  - Submodel Registry (submodel-registry-log-mem)   -> Porta 8083        |
|  - AAS Discovery Service (aas-discovery)           -> Porta 8084        |
|  - BaSyx Web UI Oficial (aas-gui)                  -> Porta 3000        |
+------------------------------------+------------------------------------+
                                     | GET /submodels/{id}/submodel-elements (Polling 500ms)
                                     v
+-------------------------------------------------------------------------+
|             CAMADA 3: FRONTEND DIGITAL TWIN 3D (index.html)             |
|  - Cena 3D Procedural (Three.js): Mesa X/Y, Pórtico, Spindle Z e Fresa  |
|  - Sincronismo Físico: Rotação (RPM), Vibração (Torque + Tool Wear)     |
|  - Alertas Visuais: Luz vermelha piscante (Falha) e Peça em Brasa (HDF) |
|  - HUD Flutuante: Temperaturas (K/°C), Potência (W), Strain e Badges    |
+-------------------------------------------------------------------------+
```

---

## 2. Estrutura de Arquivos do Projeto

```text
cnc_machine/
├── aas/
│   └── milling_machine_aas.json  # Definição AAS v3.0 (MillingMachine_AI4I + AI4I_OperationalTelemetry)
├── ai4i2020.csv                  # Dataset oficial AI4I 2020 Predictive Maintenance (10.000 registros)
├── cnc_simulator.py              # Simulador IIoT em Python (lê o CSV ou simula fisicamente a 1 Hz)
├── docker-compose.yml            # Stack Docker do Eclipse BaSyx v2 (InMemory, sem dependências pesadas)
├── index.html                    # Painel 3D do Operador (Three.js + HUD CSS + Cliente Fetch BaSyx)
└── README.md                     # Documentação completa do projeto
```

---

## 3. Modelo de Dados (AI4I 2020 no Submodelo AAS)

O Asset Administration Shell **`MillingMachine_AI4I`** (`urn:ai4i:aas:milling-machine:001`) possui o submodelo **`AI4I_OperationalTelemetry`** (`urn:ai4i:submodel:operational-telemetry:001`) contendo:

### Propriedades Estáticas / Parâmetros do Ciclo
- **`UID`**: Identificador único da linha/ciclo (`1` a `10000`).
- **`ProductID`**: Código serial do produto usinado (ex.: `M14860`, `L47181`, `H29424`).
- **`Type`**: Variante de qualidade da peça (`L` = Low 50%, `M` = Medium 30%, `H` = High 20%).

### Sensores Dinâmicos
- **`AirTemperature` [K]**: Temperatura ambiente ao redor de $300\text{ K}$ ($\approx 26.8^\circ\text{C}$).
- **`ProcessTemperature` [K]**: Temperatura do processo de usinagem ($\approx \text{AirTemperature} + 10\text{ K}$).
- **`RotationalSpeed` [rpm]**: Velocidade de rotação do eixo-árvore (*Spindle*).
- **`Torque` [Nm]**: Torque aplicado na ferramenta de corte.
- **`ToolWear` [min]**: Tempo acumulado de desgaste da fresa ($0$ a $240\text{ min}$).
- **`MechanicalPower` [W]**: Potência mecânica calculada em tempo real ($P = \tau \cdot \omega$).

### Flags Binários de Falha (`0` = Normal, `1` = Falha Ativa)
- **`MachineFailure`**: Indicador global de falha da fresadora.
- **`TWF` (*Tool Wear Failure*)**: Falha ou substituição por desgaste crítico da ferramenta ($200\text{–}240\text{ min}$).
- **`HDF` (*Heat Dissipation Failure*)**: Falha de dissipação de calor ($\Delta T < 8.6\text{ K}$ e $\text{RPM} < 1380$).
- **`PWF` (*Power Failure*)**: Potência mecânica fora dos limites operacionais ($< 3500\text{ W}$ ou $> 9000\text{ W}$).
- **`OSF` (*Overstrain Failure*)**: Sobrecarga mecânica ($\text{ToolWear} \times \text{Torque}$ acima de $11.000\text{ L}$, $12.000\text{ M}$ ou $13.000\text{ H }\text{min}\cdot\text{Nm}$).
- **`RNF` (*Random Failure*)**: Falha estocástica do processo ($0.1\%$).

---

## 4. Pré-requisitos

1. **Docker** e **Docker Compose** (ex.: Docker Desktop instalado e em execução).
2. **Python 3.9+** instalado na máquina.
3. Biblioteca Python `requests`:
   ```powershell
   pip install requests
   ```
4. Um navegador web moderno com suporte a WebGL (Chrome, Edge, Firefox).

---

## 5. Como Executar o Projeto Passo a Passo

### Passo 1: Iniciar a Infraestrutura Eclipse BaSyx (Docker)

Na pasta raiz do projeto, suba os containers com:

```powershell
docker compose up -d
```

Verifique se todos os 5 containers estão rodando (`Up`):

```powershell
docker compose ps
```

| Serviço | Container | Porta Local | Função |
| :--- | :--- | :--- | :--- |
| **AAS & Submodel Server** | `basyx-aas-environment` | `http://localhost:8081` | Hospeda o AAS e o Submodelo da Fresadora |
| **AAS Registry** | `basyx-aas-registry` | `http://localhost:8082` | Registro de Asset Administration Shells |
| **Submodel Registry** | `basyx-submodel-registry` | `http://localhost:8083` | Registro de Submodelos |
| **AAS Discovery** | `basyx-aas-discovery` | `http://localhost:8084` | Lookup de `ProductID` $\rightarrow$ `AAS ID` |
| **BaSyx Web UI** | `basyx-web-ui` | `http://localhost:3000` | Interface gráfica oficial do Eclipse BaSyx |

---

### Passo 2: Executar o Simulador Python (`cnc_simulator.py`)

Com os containers ativos, inicie o simulador em um terminal:

```powershell
python cnc_simulator.py
```

O script realizará automaticamente:
1. O provisionamento do arquivo `aas/milling_machine_aas.json` no servidor BaSyx (`http://localhost:8081`) e o registro nos serviços de Registry/Discovery.
2. O carregamento das `10.000` linhas reais do arquivo `ai4i2020.csv`.
3. O envio contínuo das propriedades a cada **1 segundo** (`[BASYX:OK]`).

#### Opções úteis de linha de comando do `cnc_simulator.py`:
- **Modo padrão (com `--fault-boost 0.15`)**: Intercala $15\%$ de linhas reais de falha do próprio `ai4i2020.csv` para que você visualize anomalias com frequência no Digital Twin sem esperar dezenas de minutos.
- **Leitura 100% sequencial do CSV começando perto da 1ª falha (`UDI=51` é `PWF`)**:
  ```powershell
  python cnc_simulator.py --fault-boost 0 --start-uid 48
  ```
- **Começar direto próximo a uma falha de calor `HDF` real do CSV (`UDI=4222`)**:
  ```powershell
  python cnc_simulator.py --fault-boost 0 --start-uid 4219
  ```
- **Alterar a velocidade de envio (ex.: atualizar a cada 0.5s)**:
  ```powershell
  python cnc_simulator.py --interval 0.5
  ```

---

### Passo 3: Abrir o Frontend Digital Twin 3D (`index.html`)

Abra o arquivo `index.html` diretamente no navegador (duplo clique) ou utilize um servidor HTTP local (ex.: extensão *Live Server* do VS Code ou `python -m http.server 8000` acessando `http://localhost:8000`).

#### Comportamento do Digital Twin 3D:
- **Standby Seguro**: Enquanto o BaSyx estiver desligado, a máquina permanece parada (`0 rpm`) e o painel indica `AGUARDANDO BASYX (OFFLINE)`.
- **Operação Sincronizada (`BASYX AAS: ONLINE`)**:
  - Faz *polling* HTTP a cada `500ms` no endpoint do Submodel Server do BaSyx.
  - A velocidade angular da fresa 3D varia proporcionalmente ao `RotationalSpeed [rpm]`.
  - O cabeçote do eixo Z aplica micro-vibrações (*chatter*) proporcionais ao `Torque [Nm]` e ao `ToolWear [min]`.
- **Reações Visuais a Falhas**:
  - **`MachineFailure == 1`**: O giroflex no topo do pórtico pisca em vermelho intenso e a ferramenta de corte muda para vermelho crítico.
  - **`HDF == 1` (Falha de Dissipação de Calor)**: A peça metálica presa na morsa sobre a mesa X/Y muda seu material para **vermelho incandescente / brasa pulsante**, simulando o superaquecimento por falta de refrigeração/dissipação.
- **Botões de Teste Rápido no Rodapé**: Permitem forçar visualmente os estados `HDF`, `TWF` e `OSF` a qualquer momento ou retornar ao modo `SINCRONIZADO COM BASYX AAS (TEMPO REAL)`.

---

## 6. Comandos de Manutenção e Diagnóstico

- **Testar manualmente a resposta JSON do Submodelo no BaSyx via terminal**:
  ```powershell
  curl http://localhost:8081/submodels/dXJuOmFpNGk6c3VibW9kZWw6b3BlcmF0aW9uYWwtdGVsZW1ldHJ5OjAwMQ/submodel-elements
  ```
- **Visualizar logs do servidor BaSyx**:
  ```powershell
  docker compose logs -f aas-environment
  ```
- **Parar e remover os containers**:
  ```powershell
  docker compose down --remove-orphans
  ```
