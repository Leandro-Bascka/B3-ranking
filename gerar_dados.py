# ==============================================================================
# GERADOR DO RANKING PARA O SITE
# Roda o mesmo motor do app (b3_core.py), sem interface, e grava:
#   docs/data.json                    -> lido pela página docs/index.html
#   docs/historico/AAAA-MM-DD.json    -> um resumo por dia (base para backtest)
#
# Uso local:   python gerar_dados.py
# Ver no PC:   python -m http.server 8000 --directory docs   (abra http://localhost:8000)
# ==============================================================================

import datetime
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

import b3_core as core

try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:  # sem tzdata: usa UTC-3 fixo (o Brasil não tem mais horário de verão)
    FUSO = datetime.timezone(datetime.timedelta(hours=-3))

BASE = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(BASE, "docs")

MIN_SUCESSOS = 20        # abaixo disso, não sobrescreve o data.json anterior
DIAS_GRAFICO = 120       # pregões no gráfico de cada ação


def tentar(funcao, vezes=3, espera=5):
    """Chama a função até devolver algo não vazio (rede de nuvem às vezes falha)."""
    for i in range(vezes):
        try:
            r = funcao()
        except Exception as e:
            print(f"   tentativa {i+1} falhou: {e}")
            r = None
        vazio = r is None or (hasattr(r, "empty") and r.empty)
        if not vazio:
            return r
        if i < vezes - 1:
            time.sleep(espera * (i + 1))
    return None


def limpo(v):
    """Converte tipos numpy/pandas em JSON puro (NaN e infinito viram null)."""
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    if v is None or v is pd.NaT:
        return None
    return v


def lista(texto):
    return [t.strip() for t in str(texto).split(" | ") if t.strip()]


def serie(valores, casas=2):
    return [None if pd.isna(x) else round(float(x), casas) for x in valores]


def montar_acao(r):
    df = core.HIST_CACHE.get(r["AÇÃO"])
    grafico = None
    if df is not None and len(df) > 0:
        t = df.tail(DIAS_GRAFICO)
        grafico = {
            "datas": [d.strftime("%Y-%m-%d") for d in t.index],
            "close": serie(t["Close"]),
            "ema50": serie(t["EMA50"]),
            "ema200": serie(t["EMA200"]),
        }
    return {
        "acao": r["AÇÃO"],
        "preco": limpo(r["PREÇO"]),
        "score": limpo(r["SCORE"]),
        "score_tec": limpo(r["SCORE_TEC"]),
        "score_val": limpo(r["SCORE_VAL"]),
        "ajuste": limpo(r["AJUSTE_CENARIO"]),
        "sinal": r["SINAL"],
        "setup": r["SETUP"],
        "tendencia": r["TENDÊNCIA"],
        "momento": r["MOMENTO"],
        "rsi": limpo(r["RSI"]),
        "rsi_status": r["RSI_STATUS"],
        "macd_status": r["MACD_STATUS"],
        "adx": limpo(r["ADX"]),
        "vol_rel": limpo(r["VOL_REL"]),
        "dist_ema50": limpo(r["DIST_EMA50_%"]),
        "dist_res": limpo(r["DIST_RES_%"]),
        "pl": limpo(r["PL"]),
        "pvp": limpo(r["PVP"]),
        "ev_ebitda": limpo(r["EV_EBITDA"]),
        "roe": limpo(r["ROE"]),
        "mrg_liq": limpo(r["MRG_LIQ"]),
        "div_patrim": limpo(r["DIV_PATRIM"]),
        "dy": limpo(r["DY"]),
        "cresc_rec": limpo(r["CRESC_REC"]),
        "beta": limpo(r["BETA"]),
        "forca_rel": limpo(r["FORCA_REL"]),
        "motivos": lista(r["MOTIVOS"]),
        "motivos_val": lista(r["MOTIVOS_VAL"]),
        "motivos_cen": lista(r["MOTIVOS_CEN"]),
        # pullback, Fibonacci e plano da operação
        "estagio": r["ESTAGIO"],
        "pb_score": limpo(r["PB_SCORE"]),
        "retr_fib": limpo(r["RETR_FIB"]),
        "nivel_fib": r["NIVEL_FIB"] if isinstance(r["NIVEL_FIB"], str) else None,
        "candle": r["CANDLE"] if isinstance(r["CANDLE"], str) else None,
        "atr_pct": limpo(r["ATR_PCT"]),
        "fib": r["FIB"] if isinstance(r["FIB"], dict) else None,
        "plano": r["PLANO"] if isinstance(r["PLANO"], dict) else None,
        "hist_pb": r["HIST_PB"] if isinstance(r["HIST_PB"], dict) else None,
        "oportunidade": bool(r["OPORTUNIDADE"]),
        "op_score": limpo(r["OP_SCORE"]),
        "op_notas": lista(r["OP_NOTAS"]),
        "pb_motivos": lista(r["PB_MOTIVOS"]),
        "novo_hoje": False,
        "grafico": grafico,
    }


def carregar_anterior(saida, data_pregao):
    """Resumo do último pregão gravado antes de data_pregao (para marcar 'novo hoje')."""
    pasta = os.path.join(saida, "historico")
    if not os.path.isdir(pasta) or not data_pregao:
        return None
    arquivos = sorted(f for f in os.listdir(pasta) if f.endswith(".json") and f[:-5] < data_pregao)
    if not arquivos:
        return None
    try:
        with open(os.path.join(pasta, arquivos[-1]), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def escrever_json(caminho, obj):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, caminho)


def main(saida=SAIDA):
    print("=" * 70)
    print("GERADOR DO RANKING B3")
    print("=" * 70)

    print("Calculando cenário de mercado...")
    regime = tentar(core.calcular_regime)
    if regime is None:
        print("AVISO: cenário indisponível; o ranking sairá só com a técnica.")
    else:
        print(f"Regime: {regime['REGIME']} ({regime['PONTOS']:+d})")

    print("Obtendo universo e fundamentos (Fundamentus)...")
    universo = tentar(core.obter_acoes_mais_liquidas)
    if universo is None:
        print("ERRO: não foi possível obter a lista de ações. data.json anterior mantido.")
        return 1

    total = len(universo)
    resultados, falhas = [], []
    for i, linha in universo.iterrows():
        acao = str(linha["AÇÃO"]).strip().upper()
        r = tentar(lambda: core.analisar_ativo(acao, linha["LIQUIDEZ"], fund=linha.to_dict(), regime=regime),
                   vezes=2, espera=3)
        if r is None:
            falhas.append(acao)
            print(f"[{i+1:02d}/{total}] {acao:<8} sem histórico")
        else:
            resultados.append(r)
            print(f"[{i+1:02d}/{total}] {acao:<8} {r['SCORE']:>5.1f}  {r['SINAL']}")
        time.sleep(core.PAUSA)

    print(f"\nSucessos: {len(resultados)} | Falhas: {len(falhas)}")
    if len(resultados) < MIN_SUCESSOS:
        print(f"ERRO: menos de {MIN_SUCESSOS} ações analisadas. data.json anterior mantido.")
        return 1

    ranking = pd.DataFrame(resultados).sort_values(
        by=["SCORE", "DIST_EMA50_%"], ascending=[False, True]).reset_index(drop=True)

    datas = [df.index[-1] for df in core.HIST_CACHE.values() if len(df)]
    data_pregao = max(datas).strftime("%Y-%m-%d") if datas else None

    regime_json = None
    if regime is not None:
        w_tec, w_val, mult = core.PESOS_REGIME[regime["REGIME"]]
        regime_json = {
            "nome": regime["REGIME"],
            "pontos": int(regime["PONTOS"]),
            "detalhes": regime["DETALHES"],
            "pesos": {"tec": w_tec, "val": w_val, "mult": mult},
        }

    dados = {
        "versao": "3.1",
        "gerado_em": datetime.datetime.now(FUSO).isoformat(timespec="minutes"),
        "data_pregao": data_pregao,
        "regime": regime_json,
        "acoes": [montar_acao(r) for _, r in ranking.iterrows()],
        "falhas": falhas,
    }
    # 'novo hoje': virou oportunidade desde o último pregão gravado que já tinha essa informação
    anterior = carregar_anterior(saida, data_pregao)
    if anterior and any("op" in a for a in anterior.get("acoes", [])):
        ops_ant = {a["acao"] for a in anterior["acoes"] if a.get("op")}
        for a in dados["acoes"]:
            a["novo_hoje"] = bool(a["oportunidade"] and a["acao"] not in ops_ant)
    dados["n_oportunidades"] = sum(1 for a in dados["acoes"] if a["oportunidade"])

    escrever_json(os.path.join(saida, "data.json"), dados)

    resumo = [{
        "acao": a["acao"], "preco": a["preco"], "score": a["score"],
        "score_tec": a["score_tec"], "score_val": a["score_val"], "sinal": a["sinal"],
        "op": a["oportunidade"], "estagio": a["estagio"], "pb_score": a["pb_score"],
        "regime": regime_json["nome"] if regime_json else None,
    } for a in dados["acoes"]]
    if data_pregao:
        escrever_json(os.path.join(saida, "historico", f"{data_pregao}.json"),
                      {"data_pregao": data_pregao, "acoes": resumo})

    print(f"Gravado: {os.path.join(saida, 'data.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
