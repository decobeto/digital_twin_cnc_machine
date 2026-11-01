#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
SIMULADOR IIoT DE FRESADORA CNC - AI4I 2020 PREDICTIVE MAINTENANCE DATASET
Integração Direta com Eclipse BaSyx v2 (Asset Administration Shell API v3.0)
===============================================================================

Funcionalidades:
1. Lê um arquivo CSV real do 'AI4I 2020 Predictive Maintenance Dataset' (se presente)
   OU simula probabilisticamente a física exata descrita no artigo original de
   Stephan Matzka (2020) a cada 1 segundo.
2. Provisiona automaticamente o AAS e o Submodelo no Eclipse BaSyx (caso ainda
   não existam) e registra no AAS Discovery Service.
3. Atualiza todas as propriedades estáticas e dinâmicas via HTTP REST ($value)
   no Submodel Server do BaSyx.
4. Transmite instantaneamente os flags binários de falha (MachineFailure, TWF,
   HDF, PWF, OSF, RNF) quando ocorrem anomalias operacionais.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional

import requests

# Configuração de Logging Industrial
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | [CNC-AI4I-SIM] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("CNC_Simulator")


# ==============================================================================
# IDENTIFICADORES DO AAS E SUBMODELO (Padrão IDTA / BaSyx v2)
# ==============================================================================
AAS_ID = "urn:ai4i:aas:milling-machine:001"
SUBMODEL_ID = "urn:ai4i:submodel:operational-telemetry:001"
AAS_JSON_PATH = Path(__file__).parent / "aas" / "milling_machine_aas.json"


def encode_b64url(identifier: str) -> str:
    """Codifica um identificador URN em Base64URL (sem padding '=') conforme especificação AAS API v3.0."""
    encoded = base64.urlsafe_b64encode(identifier.encode("utf-8")).decode("utf-8")
    return encoded.rstrip("=")


# ==============================================================================
# MODELO DE DADOS DO DATASET AI4I 2020
# ==============================================================================
@dataclass
class AI4IDataPoint:
    UID: int
    ProductID: str
    Type: str                   # 'L', 'M', ou 'H'
    AirTemperature: float       # [K]
    ProcessTemperature: float   # [K]
    RotationalSpeed: int        # [rpm]
    Torque: float               # [Nm]
    ToolWear: int               # [min]
    MachineFailure: int         # 0 ou 1
    TWF: int                    # Tool Wear Failure (0 ou 1)
    HDF: int                    # Heat Dissipation Failure (0 ou 1)
    PWF: int                    # Power Failure (0 ou 1)
    OSF: int                    # Overstrain Failure (0 ou 1)
    RNF: int                    # Random Failure (0 ou 1)

    @property
    def mechanical_power_w(self) -> float:
        """Calcula a potência mecânica no eixo-árvore (Spindle): P = Torque * omega [W]."""
        omega_rad_s = self.RotationalSpeed * (2.0 * math.pi / 60.0)
        return round(self.Torque * omega_rad_s, 1)


# ==============================================================================
# GERADOR FÍSICO-PROBABILÍSTICO FIEL AO PAPER AI4I 2020 + LEITOR CSV
# ==============================================================================
class AI4IDatasetGenerator:
    """
    Reproduz o comportamento estocástico e determinístico do dataset AI4I 2020:
    - Air Temperature: Random walk ao redor de 300 K (SD = 2 K)
    - Process Temperature: Air Temperature + 10 K + Random walk (SD = 1 K)
    - Rotational Speed: Calculado a partir de potência nominal de 2860 W + ruído
    - Torque: Normalmente distribuído ao redor de 40 Nm (SD = 10 Nm, >= 0)
    - Tool Wear: Incrementa +2 (L), +3 (M), +5 (H) min por ciclo
    - Modos de falha: TWF, HDF, PWF, OSF, RNF baseados nas regras físicas reais.
      Inclui um modo opcional de injeção periódica de anomalias para facilitar
      a visualização e demonstração no Digital Twin 3D.
    """

    def __init__(self, demo_anomaly_probability: float = 0.22):
        self.uid_counter = 0
        self.air_temp = 298.1
        self.proc_temp_offset = 10.5
        self.tool_wear = 0
        self.tool_failure_threshold = random.randint(200, 240)
        self.demo_anomaly_probability = demo_anomaly_probability

    def next_synthetic_point(self) -> AI4IDataPoint:
        self.uid_counter = (self.uid_counter % 10000) + 1

        # 1. Seleção da variante de qualidade: L (50%), M (30%), H (20%)
        rnd_type = random.random()
        if rnd_type < 0.50:
            prod_type = "L"
            wear_increment = 2
            osf_threshold = 11000.0
        elif rnd_type < 0.80:
            prod_type = "M"
            wear_increment = 3
            osf_threshold = 12000.0
        else:
            prod_type = "H"
            wear_increment = 5
            osf_threshold = 13000.0

        product_id = f"{prod_type}{14859 + self.uid_counter}"

        # 2. Random Walk das Temperaturas [K]
        self.air_temp += random.gauss(0.0, 0.35)
        # Mantém orbitando ~300 K com desvio padrão ~2 K
        self.air_temp = max(295.3, min(304.5, self.air_temp + 0.08 * (300.0 - self.air_temp)))

        self.proc_temp_offset += random.gauss(0.0, 0.25)
        self.proc_temp_offset = max(7.5, min(12.2, self.proc_temp_offset + 0.1 * (10.0 - self.proc_temp_offset)))
        proc_temp = self.air_temp + self.proc_temp_offset

        # 3. Torque [Nm] e Velocidade de Rotação [rpm]
        torque = max(3.8, random.gauss(40.0, 10.0))
        omega = 2860.0 / max(torque, 10.0)
        rpm_base = (omega * 60.0) / (2.0 * math.pi)
        rpm = int(max(1168, min(2886, rpm_base + random.gauss(0.0, 110.0))))

        # 4. Injeção controlada de cenários de falha para demonstração rica do Digital Twin
        if random.random() < self.demo_anomaly_probability:
            scenario = random.choice(["HDF", "PWF_HIGH", "OSF", "TWF", "RNF"])
            if scenario == "HDF":
                # HDF: Diferença de temperatura < 8.6 K e RPM < 1380
                self.air_temp = round(random.uniform(301.8, 303.8), 1)
                proc_temp = round(self.air_temp + random.uniform(6.2, 8.3), 1)
                rpm = random.randint(1210, 1370)
                torque = round(random.uniform(44.0, 56.0), 1)
            elif scenario == "PWF_HIGH":
                # PWF: Potência > 9000 W (ex.: alto torque e alta rotação)
                torque = round(random.uniform(62.0, 75.0), 1)
                rpm = random.randint(1450, 1680)
            elif scenario == "OSF":
                # OSF: ToolWear * Torque > limiar da variante (11k / 12k / 13k)
                self.tool_wear = random.randint(185, 215)
                torque = round((osf_threshold / self.tool_wear) + random.uniform(4.0, 12.0), 1)
                rpm = random.randint(1320, 1480)
            elif scenario == "TWF":
                # TWF: Desgaste da ferramenta atinge o limite crítico (200 - 240 min)
                self.tool_wear = self.tool_failure_threshold

        # 5. Avaliação Determinística/Física das 5 Flags de Falha (Regras AI4I 2020)
        # TWF: Tool Wear Failure (entre 200 e 240 min)
        twf = 1 if self.tool_wear >= self.tool_failure_threshold else 0

        # HDF: Heat Dissipation Failure (Temp diff < 8.6 K e RPM < 1380)
        temp_diff = round(proc_temp - self.air_temp, 2)
        hdf = 1 if (temp_diff < 8.6 and rpm < 1380) else 0

        # PWF: Power Failure (Potência mecânica < 3500 W ou > 9000 W)
        power_w = torque * (rpm * 2.0 * math.pi / 60.0)
        pwf = 1 if (power_w < 3500.0 or power_w > 9000.0) else 0

        # OSF: Overstrain Failure (ToolWear * Torque > Limiar L/M/H)
        osf = 1 if (self.tool_wear * torque > osf_threshold) else 0

        # RNF: Random Failure (0.1% natural ou quando sorteado)
        rnf = 1 if random.random() < 0.004 else 0

        # Machine Failure global (OR lógico de todas as falhas)
        machine_failure = 1 if (twf or hdf or pwf or osf or rnf) else 0

        point = AI4IDataPoint(
            UID=self.uid_counter,
            ProductID=product_id,
            Type=prod_type,
            AirTemperature=round(self.air_temp, 1),
            ProcessTemperature=round(proc_temp, 1),
            RotationalSpeed=int(rpm),
            Torque=round(torque, 1),
            ToolWear=int(self.tool_wear),
            MachineFailure=machine_failure,
            TWF=twf,
            HDF=hdf,
            PWF=pwf,
            OSF=osf,
            RNF=rnf,
        )

        # Incrementa ou reseta o desgaste da ferramenta (troca de ferramenta após TWF/OSF)
        if twf == 1 or self.tool_wear >= 240:
            self.tool_wear = 0
            self.tool_failure_threshold = random.randint(200, 240)
        else:
            self.tool_wear += wear_increment

        return point

    @staticmethod
    def iterate_csv(
        csv_path: Path,
        start_uid: int = 1,
        fault_boost_prob: float = 0.0,
    ) -> Iterator[AI4IDataPoint]:
        """
        Lê o arquivo CSV oficial 'ai4i2020.csv' em loop contínuo.
        Suporta iniciar a partir de um UID específico (ex.: --start-uid 50 para ver a primeira PWF logo no início)
        ou intercalar probabilisticamente linhas reais de falha do próprio CSV (--fault-boost) para demos!
        """
        with csv_path.open("r", encoding="utf-8-sig") as f:
            all_rows = [
                AI4IDataPoint(
                    UID=int(row["UDI"]),
                    ProductID=str(row["Product ID"]).strip(),
                    Type=str(row["Type"]).strip(),
                    AirTemperature=float(row["Air temperature [K]"]),
                    ProcessTemperature=float(row["Process temperature [K]"]),
                    RotationalSpeed=int(row["Rotational speed [rpm]"]),
                    Torque=float(row["Torque [Nm]"]),
                    ToolWear=int(row["Tool wear [min]"]),
                    MachineFailure=int(row["Machine failure"]),
                    TWF=int(row["TWF"]),
                    HDF=int(row["HDF"]),
                    PWF=int(row["PWF"]),
                    OSF=int(row["OSF"]),
                    RNF=int(row["RNF"]),
                )
                for row in csv.DictReader(f)
            ]

        fault_rows = [r for r in all_rows if r.MachineFailure == 1 or r.RNF == 1]
        start_idx = max(0, min(len(all_rows) - 1, start_uid - 1))

        logger.info(
            "Dataset 'ai4i2020.csv' carregado em memória: %d linhas totais (%d registros de falha reais).",
            len(all_rows),
            len(fault_rows),
        )

        while True:
            for idx in range(start_idx, len(all_rows)):
                if fault_boost_prob > 0.0 and fault_rows and random.random() < fault_boost_prob:
                    yield random.choice(fault_rows)
                else:
                    yield all_rows[idx]
            start_idx = 0


# ==============================================================================
# CLIENTE REST DO ECLIPSE BASYX (AAS SERVER / SUBMODEL SERVER / DISCOVERY)
# ==============================================================================
class BaSyxAASClient:
    """
    Gerencia a comunicação HTTP REST com o Eclipse BaSyx v2 (API v3.0):
    - AAS Environment (AAS Server + Submodel Server): porta 8081
    - AAS Registry: porta 8082
    - Submodel Registry: porta 8083
    - AAS Discovery Service: porta 8084
    """

    def __init__(
        self,
        aas_env_url: str,
        aas_registry_url: str = "http://localhost:8082",
        sm_registry_url: str = "http://localhost:8083",
        discovery_url: str = "http://localhost:8084",
    ):
        self.aas_env_url = aas_env_url.rstrip("/")
        self.aas_registry_url = aas_registry_url.rstrip("/")
        self.sm_registry_url = sm_registry_url.rstrip("/")
        self.discovery_url = discovery_url.rstrip("/")
        self.aas_id_b64 = encode_b64url(AAS_ID)
        self.submodel_id_b64 = encode_b64url(SUBMODEL_ID)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def ensure_aas_and_submodel_provisioned(self) -> bool:
        """
        Verifica se o AAS e o Submodelo já estão carregados no servidor BaSyx (porta 8081).
        Caso o servidor tenha acabado de subir em memória (404), cria automaticamente
        o AAS e o Submodelo a partir de './aas/milling_machine_aas.json' e registra os
        descritores no AAS Registry (8082), Submodel Registry (8083) e Discovery (8084).
        """
        sm_url = f"{self.aas_env_url}/submodels/{self.submodel_id_b64}"
        try:
            resp = self.session.get(sm_url, timeout=3.0)
            if resp.status_code == 200:
                logger.info("Submodelo '%s' confirmado online no BaSyx (%s).", SUBMODEL_ID, sm_url)
                self._register_descriptors_and_discovery("M14860")
                return True

            if resp.status_code == 404 and AAS_JSON_PATH.exists():
                logger.info("Provisionando AAS 'MillingMachine_AI4I' e Submodelo no BaSyx via REST...")
                env_data = json.loads(AAS_JSON_PATH.read_text(encoding="utf-8"))

                for aas in env_data.get("assetAdministrationShells", []):
                    r_aas = self.session.post(f"{self.aas_env_url}/shells", json=aas, timeout=3.0)
                    logger.info("POST /shells -> HTTP %d", r_aas.status_code)

                for sm in env_data.get("submodels", []):
                    r_sm = self.session.post(f"{self.aas_env_url}/submodels", json=sm, timeout=3.0)
                    logger.info("POST /submodels -> HTTP %d", r_sm.status_code)

                self._register_descriptors_and_discovery("M14860")
                return True
        except requests.RequestException as exc:
            logger.warning(
                "Servidor BaSyx indisponível em %s (%s). Verifique se 'docker compose up -d' está ativo...",
                self.aas_env_url,
                exc,
            )
        return False

    def _register_descriptors_and_discovery(self, product_id: str) -> None:
        """
        Registra o AAS Descriptor no AAS Registry (8082), o Submodel Descriptor no
        Submodel Registry (8083) e o Asset Link no AAS Discovery Service (8084) via REST.
        Isso evita depender do auto-registro em background do Java que causava ClosedChannelException!
        """
        # 1. Registra no AAS Registry (8082)
        aas_descriptor = {
            "idShort": "MillingMachine_AI4I",
            "id": AAS_ID,
            "globalAssetId": "urn:ai4i:asset:milling-machine:001",
            "endpoints": [
                {
                    "interface": "AAS-3.0",
                    "protocolInformation": {
                        "href": f"{self.aas_env_url}/shells/{self.aas_id_b64}",
                        "endpointProtocol": "HTTP",
                    },
                }
            ],
        }
        self._register(
            "AAS Registry",
            f"{self.aas_registry_url}/shell-descriptors",
            aas_descriptor,
            update_url=f"{self.aas_registry_url}/shell-descriptors/{self.aas_id_b64}",
        )

        # 2. Registra no Submodel Registry (8083)
        sm_descriptor = {
            "idShort": "AI4I_OperationalTelemetry",
            "id": SUBMODEL_ID,
            "endpoints": [
                {
                    "interface": "SUBMODEL-3.0",
                    "protocolInformation": {
                        "href": f"{self.aas_env_url}/submodels/{self.submodel_id_b64}",
                        "endpointProtocol": "HTTP",
                    },
                }
            ],
        }
        self._register(
            "Submodel Registry",
            f"{self.sm_registry_url}/submodel-descriptors",
            sm_descriptor,
            update_url=f"{self.sm_registry_url}/submodel-descriptors/{self.submodel_id_b64}",
        )

        # 3. Registra no Discovery Service (8084)
        discovery_endpoint = f"{self.discovery_url}/lookup/shells/{self.aas_id_b64}"
        payload = [
            {"name": "globalAssetId", "value": "urn:ai4i:asset:milling-machine:001"},
            {"name": "ProductID", "value": product_id},
        ]
        self._register("Discovery", discovery_endpoint, payload)

    def _register(self, service: str, url: str, payload: object, update_url: Optional[str] = None) -> None:
        """
        POST de registro com log do resultado. HTTP 409 significa que o recurso já existe:
        se houver 'update_url', o descritor é atualizado via PUT; senão, apenas registra no log.
        """
        try:
            resp = self.session.post(url, json=payload, timeout=1.5)
            if resp.status_code == 409 and update_url:
                resp = self.session.put(update_url, json=payload, timeout=1.5)
                action = "atualizado"
            elif resp.status_code == 409:
                logger.info("%s: já registrado (HTTP 409).", service)
                return
            else:
                action = "registrado"

            if resp.ok:
                logger.info("%s: %s (HTTP %d).", service, action, resp.status_code)
            else:
                logger.warning(
                    "%s: falha no registro (HTTP %d) em %s: %s",
                    service, resp.status_code, resp.url, resp.text[:300],
                )
        except requests.RequestException as exc:
            logger.warning("%s: indisponível em %s (%s).", service, url, exc)

    def update_property_value(self, id_short: str, value: object) -> bool:
        """
        Atualiza o valor bruto ($value) de uma propriedade no Submodel Server do BaSyx.
        Na especificação AAS API v3.0:
          PATCH /submodels/{submodelIdentifier}/submodel-elements/{idShortPath}/$value
        """
        url = (
            f"{self.aas_env_url}/submodels/{self.submodel_id_b64}"
            f"/submodel-elements/{id_short}/$value"
        )
        # No padrão AAS v3.0, o endpoint /$value recebe um JSON literal (ex.: "298.1")
        json_payload = json.dumps(str(value))

        try:
            resp = self.session.patch(url, data=json_payload, timeout=1.5)
            if resp.status_code in (200, 204):
                return True
            # Fallback para servidores que usam PUT no endpoint $value
            resp_put = self.session.put(url, data=json_payload, timeout=1.5)
            return resp_put.status_code in (200, 204)
        except requests.RequestException:
            return False

    def push_telemetry(self, point: AI4IDataPoint) -> bool:
        """
        Envia todas as propriedades estáticas, dinâmicas e flags de falha do ciclo atual.
        Se houver falha ('MachineFailure == 1'), prioriza o envio imediato das flags de erro.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Se houver falha, transmite prioritariamente o estado binário de erro para o AAS
        failure_properties: Dict[str, object] = {
            "MachineFailure": point.MachineFailure,
            "TWF": point.TWF,
            "HDF": point.HDF,
            "PWF": point.PWF,
            "OSF": point.OSF,
            "RNF": point.RNF,
        }

        telemetry_properties: Dict[str, object] = {
            "UID": point.UID,
            "ProductID": point.ProductID,
            "Type": point.Type,
            "AirTemperature": point.AirTemperature,
            "ProcessTemperature": point.ProcessTemperature,
            "RotationalSpeed": point.RotationalSpeed,
            "Torque": point.Torque,
            "ToolWear": point.ToolWear,
            "MechanicalPower": point.mechanical_power_w,
            "LastUpdateTimestamp": now_iso,
        }

        # Ordem de envio: se a máquina falhou, atualiza os alarmes primeiro!
        ordered_updates = (
            {**failure_properties, **telemetry_properties}
            if point.MachineFailure == 1
            else {**telemetry_properties, **failure_properties}
        )

        success_count = 0
        for prop_name, prop_val in ordered_updates.items():
            if self.update_property_value(prop_name, prop_val):
                success_count += 1

        return success_count == len(ordered_updates)


# ==============================================================================
# LOOP PRINCIPAL DE EXECUÇÃO DO SIMULADOR
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulador IIoT da Fresadora CNC (AI4I 2020 Dataset) para Eclipse BaSyx AAS"
    )
    parser.add_argument(
        "--basyx-url",
        default=os.environ.get("BASYX_URL", "http://localhost:8081"),
        help="URL base do BaSyx AAS Environment / Submodel Server (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--discovery-url",
        default=os.environ.get("BASYX_DISCOVERY_URL", "http://localhost:8084"),
        help="URL base do BaSyx Discovery Service (default: http://localhost:8084)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("ai4i2020.csv"),
        help="Caminho opcional para o arquivo CSV real do AI4I 2020 Dataset",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Intervalo de amostragem em segundos (default: 1.0s)",
    )
    parser.add_argument(
        "--anomaly-rate",
        type=float,
        default=0.22,
        help="Probabilidade de injeção de falhas no modo sintético (0.0 a 1.0, default: 0.22)",
    )
    parser.add_argument(
        "--start-uid",
        type=int,
        default=1,
        help="UID inicial ao ler o arquivo ai4i2020.csv (ex.: 48 para ver falha PWF em 3s, 4220 para HDF)",
    )
    parser.add_argument(
        "--fault-boost",
        type=float,
        default=0.15,
        help="Probabilidade (0.0 a 1.0) de sortear uma linha real de falha do ai4i2020.csv para demonstração visual (default: 0.15; use 0.0 para leitura 100%% sequencial)",
    )
    args = parser.parse_args()

    logger.info("=" * 78)
    logger.info("INICIANDO SIMULADOR DIGITAL TWIN - MILLING MACHINE AI4I 2020")
    logger.info("BaSyx AAS/Submodel Server : %s", args.basyx_url)
    logger.info("AAS ID (Base64URL)        : %s", encode_b64url(AAS_ID))
    logger.info("Submodel ID (Base64URL)   : %s", encode_b64url(SUBMODEL_ID))
    logger.info("=" * 78)

    client = BaSyxAASClient(aas_env_url=args.basyx_url, discovery_url=args.discovery_url)
    generator = AI4IDatasetGenerator(demo_anomaly_probability=args.anomaly_rate)

    # Seleciona fonte de dados: CSV oficial (ai4i2020.csv) ou gerador probabilístico AI4I
    if args.csv.exists():
        logger.info("Arquivo CSV detectado em '%s'. Reproduzindo linhas reais do dataset.", args.csv)
        data_iterator = generator.iterate_csv(
            args.csv,
            start_uid=args.start_uid,
            fault_boost_prob=args.fault_boost,
        )
    else:
        logger.info(
            "Arquivo '%s' não encontrado localmente. Usando Simulador Físico-Probabilístico AI4I 2020.",
            args.csv,
        )
        data_iterator = iter(generator.next_synthetic_point, None)

    provisioned = client.ensure_aas_and_submodel_provisioned()

    try:
        for point in data_iterator:
            cycle_start = time.perf_counter()

            if not provisioned:
                provisioned = client.ensure_aas_and_submodel_provisioned()

            synced = client.push_telemetry(point) if provisioned else False
            if not synced:
                # Se houve perda de conexão ou reinício do container, força re-verificação no próximo ciclo
                provisioned = False

            # Formatação visual rica no terminal para acompanhamento em tempo real
            active_faults = [
                name
                for name, val in (
                    ("TWF", point.TWF),
                    ("HDF", point.HDF),
                    ("PWF", point.PWF),
                    ("OSF", point.OSF),
                    ("RNF", point.RNF),
                )
                if val == 1
            ]
            fault_str = f"ALERTA CRITICO {active_faults}" if point.MachineFailure else "OPERACAO NORMAL"
            sync_badge = "BASYX:OK" if synced else "BASYX:OFFLINE"

            log_msg = (
                f"[{sync_badge}] UID={point.UID:04d} ({point.ProductID} | Tipo {point.Type}) | "
                f"T_ar={point.AirTemperature:.1f}K | T_proc={point.ProcessTemperature:.1f}K | "
                f"RPM={point.RotationalSpeed:4d} | Torque={point.Torque:4.1f}Nm | "
                f"Wear={point.ToolWear:3d}min | Status: {fault_str}"
            )

            if point.MachineFailure:
                logger.error(log_msg)
            else:
                logger.info(log_msg)

            elapsed = time.perf_counter() - cycle_start
            sleep_time = max(0.05, args.interval - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Simulador encerrado pelo operador (Ctrl+C).")


if __name__ == "__main__":
    main()
