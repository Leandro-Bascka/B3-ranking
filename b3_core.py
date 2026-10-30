# ==============================================================================
# SCANNER QUANTITATIVO B3 V3.0  -  NÚCLEO (sem interface gráfica)
#   Usado pelo app de desktop (scanner_b3_v3.py) e pelo gerador do site (gerar_dados.py)
#   Técnica (seu score original) + Valor (fundamentos) + Cenário de mercado
# ==============================================================================
# O que mudou em relação à V2.0 (o score técnico e todos os indicadores foram
# mantidos exatamente como estavam):
#
#   1. CORREÇÃO: o filtro de liquidez pegava a 1ª coluna com "liq" no nome
#      (P/Ativ Circ.Liq), e não a "Liq.2meses". Agora usa Liq.2meses.
#   2. CORREÇÃO: PERIODO_HISTORICO "1y" gera ~247 pregões, mas o corte exigia
#      300 linhas, então todos os ativos eram descartados. Agora "2y".
#   3. NOVO: score de VALOR (0-100) com os fundamentos do próprio Fundamentus
#      (P/L, P/VP, EV/EBITDA, ROE, margem líquida, dívida, DY, crescimento).
#   4. NOVO: CENÁRIO DE MERCADO (regime) a partir do Ibovespa, dólar e Selic.
#      O regime muda o peso entre técnica e valor.
#   5. NOVO: ajuste por força relativa (vs Ibovespa) e beta, conforme o regime.
#   6. NOVO: score FINAL combinado, travas de segurança, exportação para CSV.
#
#   Isto é uma ferramenta de triagem baseada em regras, NÃO é recomendação de
#   investimento. Teste (backtest) antes de usar dinheiro real.
# ==============================================================================

import io
import time
import datetime
import unicodedata
import warnings

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

# ==============================================================================
# CONFIGURAÇÕES
# ==============================================================================

QTDE_ATIVOS = 80
PERIODO_HISTORICO = "5y"        # era "1y" (insuficiente); 5 anos dão amostra ao histórico do setup
INTERVALO = "1d"
PAUSA = 0.15

PERIODO_EMA9 = 9
PERIODO_EMA21 = 21
PERIODO_EMA50 = 50
PERIODO_EMA200 = 200
PERIODO_RSI = 14
MACD_RAPIDA = 12
MACD_LENTA = 26
MACD_SIGNAL = 9
PERIODO_ADX = 14
PERIODO_VOLUME = 20
PERIODO_RESISTENCIA = 20
DIAS_CROSS = 5
TOLERANCIA_EMA50 = 0.015

# --- Novos parâmetros (V3) ---------------------------------------------------
INDICE = "^BVSP"
DOLAR = "USDBRL=X"
PERIODO_BETA = 250              # pregões usados no cálculo do beta
PERIODO_FR = 60                 # pregões da força relativa vs Ibovespa

# --- Pullback / Fibonacci (V3.1) ---------------------------------------------
PERIODO_ATR = 14
JANELA_SWING = 120              # pregões analisados para achar a perna de alta
MAX_IDADE_ALTO = 60             # a máxima da perna precisa ser dos últimos 60 pregões
MAX_PERNA = 60                  # a perna de alta tem no máximo 60 pregões (da mínima à máxima)
AMPLITUDE_MIN = 0.08            # perna de alta mínima de 8% (da mínima à máxima)
FIB_NIVEIS = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_ZONA = (0.382, 0.618)       # zona de maior probabilidade do recuo
FIB_MAX_RETR = 0.806            # além de 78,6% (com tolerância) o pullback é considerado rompido
STOP_MAX_PCT = 10.0             # stop mais distante que isso invalida o plano
RR_MIN = 2.0                    # ganho/risco mínimo para virar oportunidade
RR_MIN_DEFENSIVO = 3.0          # idem, quando o regime é RISK-OFF
PB_MIN = 60                     # nota mínima de qualidade do pullback
VALOR_MIN = 40                  # nota de valor mínima (quando existir)
HIST_HORIZONTE = 30             # pregões para o alvo ser atingido, no histórico do setup
HIST_ALVO_R = 2.0               # alvo do histórico: 2 vezes o risco

# Pesos por regime: (peso técnica, peso valor, multiplicador do score)
PESOS_REGIME = {
    "RISK-ON":  (0.65, 0.35, 1.00),
    "NEUTRO":   (0.55, 0.45, 1.00),
    "CAUTELA":  (0.40, 0.60, 0.95),
    "RISK-OFF": (0.30, 0.70, 0.85),
}

# Travas: valor barato em tendência de baixa não vira "entrada"
TETO_TENDENCIA_BAIXA = 54       # tendência BAIXA -> no máximo "EVITAR"
TETO_TRANSICAO_DEFENSIVO = 64   # TRANSIÇÃO em CAUTELA/RISK-OFF -> no máximo "AGUARDAR"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Cache dos históricos já calculados (evita baixar de novo ao clicar no gráfico)
HIST_CACHE = {}

# ==============================================================================
# DADOS: UNIVERSO + FUNDAMENTOS (Fundamentus)
# ==============================================================================

def _norm(txt):
    """Normaliza nome de coluna: minúsculo, sem acento, sem espaços."""
    t = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode()
    return t.lower().replace(" ", "")


def _para_float(x):
    """Converte '12,34%', '1.234,56', 0.5, '-' em float (NaN se inválido)."""
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    s = str(x).strip().replace("%", "")
    if s in ("", "-", "nan", "None"):
        return np.nan
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


# nome normalizado da coluna do Fundamentus -> nome interno
COLUNAS_FUND = {
    "papel": "AÇÃO",
    "liq.2meses": "LIQUIDEZ",
    "p/l": "PL",
    "p/vp": "PVP",
    "div.yield": "DY",
    "ev/ebitda": "EV_EBITDA",
    "mrg.liq.": "MRG_LIQ",
    "roe": "ROE",
    "div.liq/patrim.": "DIV_PATRIM",
    "div.brut/patrim.": "DIV_PATRIM",
    "cresc.rec.5a": "CRESC_REC",
}


def obter_acoes_mais_liquidas():
    """Retorna as QTDE_ATIVOS ações mais líquidas (Liq.2meses) com fundamentos."""
    url = "https://www.fundamentus.com.br/resultado.php"
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        tabelas = pd.read_html(io.StringIO(response.text), decimal=",", thousands=".")
        if not tabelas:
            raise ValueError("Nenhuma tabela encontrada.")
        df = tabelas[0]

        mapa = {}
        for coluna in df.columns:
            interno = COLUNAS_FUND.get(_norm(coluna))
            if interno and interno not in mapa.values():
                mapa[coluna] = interno

        if "AÇÃO" not in mapa.values():
            raise ValueError("Coluna Papel não encontrada.")
        if "LIQUIDEZ" not in mapa.values():
            raise ValueError("Coluna Liq.2meses não encontrada.")

        out = df[list(mapa.keys())].rename(columns=mapa).copy()
        out["AÇÃO"] = out["AÇÃO"].astype(str).str.strip().str.upper()
        for c in out.columns:
            if c != "AÇÃO":
                out[c] = out[c].map(_para_float)

        # No Fundamentus, 0,00 nestes campos significa "dado ausente"
        for c in ("PL", "PVP", "EV_EBITDA", "MRG_LIQ"):
            if c in out.columns:
                out[c] = out[c].replace(0, np.nan)

        out = out.dropna(subset=["LIQUIDEZ"])
        out = out[out["LIQUIDEZ"] > 0]
        out = out.sort_values("LIQUIDEZ", ascending=False).head(QTDE_ATIVOS).reset_index(drop=True)
        return out
    except Exception as e:
        print(f"ERRO AO OBTER AÇÕES: {e}")
        return pd.DataFrame()


# ==============================================================================
# DADOS: HISTÓRICO DE PREÇOS
# ==============================================================================

def baixar_historico(acao):
    ticker = acao + ".SA"
    try:
        df = yf.download(ticker, period=PERIODO_HISTORICO, interval=INTERVALO,
                         auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            novo = pd.DataFrame(index=df.index)
            for coluna in ["Open", "High", "Low", "Close", "Volume"]:
                try:
                    serie = df[coluna]
                    if isinstance(serie, pd.DataFrame):
                        serie = serie.iloc[:, 0]
                    novo[coluna] = serie
                except Exception:
                    return None
            df = novo
        else:
            colunas = ["Open", "High", "Low", "Close", "Volume"]
            if not all(c in df.columns for c in colunas):
                return None
            df = df[colunas].copy()

        for coluna in ["Open", "High", "Low", "Close", "Volume"]:
            df[coluna] = pd.to_numeric(df[coluna], errors="coerce")

        df = df.dropna()
        if len(df) < 300:
            return None
        return df
    except Exception:
        return None


def baixar_close(ticker):
    """Série de fechamento de um índice/câmbio (sem exigir volume)."""
    try:
        df = yf.download(ticker, period=PERIODO_HISTORICO, interval=INTERVALO,
                         auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            return None
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.to_numeric(close, errors="coerce").dropna()
        return close if len(close) >= 120 else None
    except Exception:
        return None


# ==============================================================================
# INDICADORES TÉCNICOS (inalterados)
# ==============================================================================

def calcular_ema(serie, periodo):
    return serie.ewm(span=periodo, adjust=False).mean()


def calcular_rsi(close, periodo=14):
    delta = close.diff()
    ganhos = delta.where(delta > 0, 0.0)
    perdas = -delta.where(delta < 0, 0.0)
    media_ganhos = ganhos.ewm(alpha=1/periodo, adjust=False).mean()
    media_perdas = perdas.ewm(alpha=1/periodo, adjust=False).mean()
    rs = media_ganhos / media_perdas.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calcular_macd(close):
    ema12 = close.ewm(span=MACD_RAPIDA, adjust=False).mean()
    ema26 = close.ewm(span=MACD_LENTA, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histograma = macd - signal
    return macd, signal, histograma


def calcular_adx(df, periodo=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    movimento_up = high.diff()
    movimento_down = -low.diff()

    plus_dm = np.where((movimento_up > movimento_down) & (movimento_up > 0), movimento_up, 0)
    minus_dm = np.where((movimento_down > movimento_up) & (movimento_down > 0), movimento_down, 0)

    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr = tr.ewm(alpha=1/periodo, adjust=False).mean()
    plus_dm_smoothed = plus_dm.ewm(alpha=1/periodo, adjust=False).mean()
    minus_dm_smoothed = minus_dm.ewm(alpha=1/periodo, adjust=False).mean()

    plus_di = 100 * plus_dm_smoothed / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm_smoothed / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/periodo, adjust=False).mean()
    return adx, plus_di, minus_di


def calcular_indicadores(df):
    df = df.copy()
    close = df["Close"]

    df["EMA9"] = calcular_ema(close, PERIODO_EMA9)
    df["EMA21"] = calcular_ema(close, PERIODO_EMA21)
    df["EMA50"] = calcular_ema(close, PERIODO_EMA50)
    df["EMA200"] = calcular_ema(close, PERIODO_EMA200)
    df["RSI"] = calcular_rsi(close, PERIODO_RSI)
    df["MACD"], df["MACD_SIGNAL"], df["MACD_HIST"] = calcular_macd(close)
    df["ADX"], df["DI_PLUS"], df["DI_MINUS"] = calcular_adx(df, PERIODO_ADX)
    df["VOL_MEDIA"] = df["Volume"].rolling(PERIODO_VOLUME).mean()
    df["VOL_REL"] = df["Volume"] / df["VOL_MEDIA"]
    df["EMA200_30"] = df["EMA200"].shift(30)
    df["EMA200_UP"] = df["EMA200"] > df["EMA200_30"]
    df["EMA50_10"] = df["EMA50"].shift(10)
    df["EMA50_UP"] = df["EMA50"] > df["EMA50_10"]

    df["CROSS_EMA50_UP"] = (df["Close"].shift(1) <= df["EMA50"].shift(1)) & (df["Close"] > df["EMA50"])
    df["CROSS_EMA9_21"] = (df["EMA9"].shift(1) <= df["EMA21"].shift(1)) & (df["EMA9"] > df["EMA21"])
    df["CROSS_MACD"] = (df["MACD"].shift(1) <= df["MACD_SIGNAL"].shift(1)) & (df["MACD"] > df["MACD_SIGNAL"])
    df["RSI_UP"] = df["RSI"] > df["RSI"].shift(1)
    df["MACD_HIST_UP"] = df["MACD_HIST"] > df["MACD_HIST"].shift(1)

    df["RESISTENCIA20"] = df["High"].shift(1).rolling(PERIODO_RESISTENCIA).max()
    df["DIST_RESISTENCIA"] = (df["RESISTENCIA20"] - df["Close"]) / df["Close"] * 100
    df["DIST_EMA50"] = (df["Close"] - df["EMA50"]) / df["EMA50"] * 100
    df["DIST_EMA200"] = (df["Close"] - df["EMA200"]) / df["EMA200"] * 100

    df["TENDENCIA_ALTA"] = (df["Close"] > df["EMA50"]) & (df["EMA50"] > df["EMA200"]) & (df["EMA200_UP"])
    df["MEDIAS_ALINHADAS"] = (df["EMA9"] > df["EMA21"]) & (df["EMA21"] > df["EMA50"]) & (df["EMA50"] > df["EMA200"])

    df["ATR"] = calcular_atr(df)
    df["ATR_PCT"] = df["ATR"] / df["Close"] * 100
    return df


def identificar_cross_recente(df):
    ultimos = df.tail(DIAS_CROSS + 1)
    cruzamentos = ultimos[ultimos["CROSS_EMA50_UP"]]
    if cruzamentos.empty:
        return None
    data_cross = cruzamentos.index[-1]
    pos_cross = df.index.get_loc(data_cross)
    dias = (len(df) - 1) - pos_cross
    return dias


def identificar_pullback(df):
    atual = df.iloc[-1]
    ultimos = df.tail(10)
    tendencia = (ultimos["EMA50"] > ultimos["EMA200"]).mean() >= 0.70
    if not tendencia:
        return False
    distancia = ((ultimos["Low"] - ultimos["EMA50"]) / ultimos["EMA50"]).abs()
    tocou_ema50 = (distancia <= TOLERANCIA_EMA50).any()
    acima = atual["Close"] > atual["EMA50"]
    candle_positivo = atual["Close"] > df.iloc[-2]["Close"]
    return tocou_ema50 and acima and candle_positivo


def identificar_reversao(df):
    if len(df) < 10:
        return False
    atual = df.iloc[-1]
    anterior = df.iloc[-2]
    rsi_recuperando = (anterior["RSI"] < 45) and (atual["RSI"] > anterior["RSI"])
    macd_melhorando = atual["MACD_HIST"] > anterior["MACD_HIST"]
    recuperou_ema9 = atual["Close"] > atual["EMA9"]
    return rsi_recuperando and macd_melhorando and recuperou_ema9


# ==============================================================================
# SCORE TÉCNICO (inalterado)
# ==============================================================================

def score_tendencia(linha):
    score = 0
    motivos = []
    if linha["Close"] > linha["EMA50"]:
        score += 5
        motivos.append("Preço acima da EMA50")
    if linha["EMA50"] > linha["EMA200"]:
        score += 5
        motivos.append("EMA50 acima da EMA200")
    if linha["EMA200_UP"]:
        score += 5
        motivos.append("EMA200 subindo")
    if linha["EMA50_UP"]:
        score += 5
        motivos.append("EMA50 subindo")
    return score, motivos


def score_setup(linha, dias_cross, pullback, reversao):
    score = 0
    motivos = []
    if dias_cross is not None:
        if dias_cross == 0:
            score += 20
            motivos.append("Preço cruzou EMA50 hoje")
        elif dias_cross == 1:
            score += 18
            motivos.append("Preço cruzou EMA50 ontem")
        elif dias_cross <= 3:
            score += 15
            motivos.append(f"Preço cruzou EMA50 há {dias_cross} dias")
        else:
            score += 10
            motivos.append(f"Cruzamento EMA50 há {dias_cross} dias")
    if pullback:
        score += 20
        motivos.append("Pullback/reação na EMA50")
    if reversao:
        score += 8
        motivos.append("Sinais de recuperação de momentum")
    return score, motivos


def score_rsi(linha):
    score = 0
    motivos = []
    rsi = linha["RSI"]
    if linha["RSI_UP"]:
        score += 5
        motivos.append("RSI está subindo")
    if 50 <= rsi <= 60:
        score += 10
        motivos.append(f"RSI saudável ({rsi:.1f})")
    elif 45 <= rsi < 50:
        score += 8
        motivos.append(f"RSI recuperando ({rsi:.1f})")
    elif 60 < rsi <= 65:
        score += 7
        motivos.append(f"RSI forte ({rsi:.1f})")
    elif 40 <= rsi < 45:
        score += 5
        motivos.append(f"RSI ainda baixo ({rsi:.1f})")
    elif rsi > 70:
        score += 1
        motivos.append(f"RSI sobrecomprado ({rsi:.1f})")
    else:
        score += 2
        motivos.append(f"RSI fraco ({rsi:.1f})")
    return score, motivos


def score_macd(linha):
    score = 0
    motivos = []
    if linha["MACD"] > linha["MACD_SIGNAL"]:
        score += 7
        motivos.append("MACD acima da Signal")
    if linha["MACD_HIST"] > 0:
        score += 4
        motivos.append("Histograma MACD positivo")
    if linha["MACD_HIST_UP"]:
        score += 5
        motivos.append("Histograma MACD aumentando")
    if linha["CROSS_MACD"]:
        score += 4
        motivos.append("MACD cruzou Signal para cima")
    return score, motivos


def score_adx(linha):
    score = 0
    motivos = []
    adx = linha["ADX"]
    if adx >= 30:
        score += 7
        motivos.append(f"ADX muito forte ({adx:.1f})")
    elif adx >= 25:
        score += 6
        motivos.append(f"ADX forte ({adx:.1f})")
    elif adx >= 20:
        score += 4
        motivos.append(f"ADX em tendência ({adx:.1f})")
    elif adx >= 15:
        score += 2
        motivos.append(f"ADX moderado ({adx:.1f})")
    else:
        motivos.append(f"ADX fraco ({adx:.1f})")
    if linha["DI_PLUS"] > linha["DI_MINUS"]:
        score += 3
        motivos.append("DI+ acima do DI-")
    return score, motivos


def score_volume(linha):
    score = 0
    motivos = []
    volume = linha["VOL_REL"]
    if volume >= 1.50:
        score += 5
        motivos.append(f"Volume muito forte ({volume:.2f}x)")
    elif volume >= 1.20:
        score += 4
        motivos.append(f"Volume forte ({volume:.2f}x)")
    elif volume >= 1.00:
        score += 3
        motivos.append(f"Volume acima da média ({volume:.2f}x)")
    elif volume >= 0.80:
        score += 1
        motivos.append(f"Volume normal ({volume:.2f}x)")
    else:
        motivos.append(f"Volume fraco ({volume:.2f}x)")
    return score, motivos


def score_resistencia(linha):
    score = 0
    motivos = []
    distancia = linha["DIST_RESISTENCIA"]
    if distancia >= 8:
        score += 5
        motivos.append(f"{distancia:.1f}% até resistência")
    elif distancia >= 5:
        score += 4
        motivos.append(f"{distancia:.1f}% até resistência")
    elif distancia >= 3:
        score += 3
        motivos.append(f"{distancia:.1f}% até resistência")
    elif distancia >= 1.5:
        score += 1
        motivos.append(f"Apenas {distancia:.1f}% até resistência")
    else:
        score -= 3
        motivos.append("Muito próximo da resistência")
    return score, motivos


def score_distancia_ema50(linha):
    score = 0
    motivos = []
    distancia = linha["DIST_EMA50"]
    if 0 <= distancia <= 2:
        score += 5
        motivos.append(f"Preço apenas {distancia:.1f}% acima da EMA50")
    elif 2 < distancia <= 4:
        score += 3
        motivos.append(f"Preço {distancia:.1f}% acima da EMA50")
    elif 4 < distancia <= 7:
        score += 1
        motivos.append(f"Preço {distancia:.1f}% acima da EMA50")
    elif distancia > 7:
        score -= 3
        motivos.append(f"Preço esticado {distancia:.1f}% acima da EMA50")
    else:
        motivos.append("Preço abaixo da EMA50")
    return score, motivos


def classificar_sinal(score):
    if score >= 85:
        return "ENTRADA EXCELENTE"
    elif score >= 75:
        return "ENTRADA FORTE"
    elif score >= 65:
        return "ENTRADA INTERESSANTE"
    elif score >= 55:
        return "AGUARDAR CONFIRMAÇÃO"
    else:
        return "EVITAR"


# ==============================================================================
# NOVO: SCORE DE VALOR (0-100) A PARTIR DOS FUNDAMENTOS
# ==============================================================================

def _valido(v):
    return v is not None and not pd.isna(v)


def _menor_melhor(v, faixas, resto=0):
    for limite, pts in faixas:
        if v <= limite:
            return pts
    return resto


def _maior_melhor(v, faixas, resto=0):
    for limite, pts in faixas:
        if v >= limite:
            return pts
    return resto


def score_valor(f):
    """
    Pontua os fundamentos disponíveis e normaliza pelo máximo possível dos itens
    que existem, assim bancos (sem EV/EBITDA) não são punidos por dado ausente.
    Retorna (score 0-100 ou NaN, lista de motivos).
    Exige ao menos 4 indicadores válidos.
    """
    itens = []  # (pontos, máximo, motivo)

    pl = f.get("PL")
    if _valido(pl):
        if pl < 0:
            itens.append((0, 20, f"Prejuízo (P/L {pl:.1f})"))
        else:
            p = _menor_melhor(pl, [(8, 20), (12, 16), (15, 12), (20, 6), (30, 2)])
            itens.append((p, 20, f"P/L {pl:.1f}"))

    pvp = f.get("PVP")
    if _valido(pvp):
        if pvp < 0:
            itens.append((0, 15, "Patrimônio negativo"))
        else:
            p = _menor_melhor(pvp, [(1, 15), (1.5, 12), (2.5, 8), (4, 3)])
            itens.append((p, 15, f"P/VP {pvp:.2f}"))

    ev = f.get("EV_EBITDA")
    financeiro = not (_valido(ev) and ev > 0)   # banco/seguradora: sem EV/EBITDA
    if not financeiro:
        p = _menor_melhor(ev, [(4, 15), (6, 12), (9, 8), (12, 4)])
        itens.append((p, 15, f"EV/EBITDA {ev:.1f}"))

    roe = f.get("ROE")
    if _valido(roe):
        if roe > 60:
            itens.append((5, 15, f"ROE anômalo ({roe:.0f}%)"))
        else:
            p = _maior_melhor(roe, [(20, 15), (15, 12), (10, 8), (5, 3)])
            itens.append((p, 15, f"ROE {roe:.1f}%"))

    ml = f.get("MRG_LIQ")
    if _valido(ml):
        p = _maior_melhor(ml, [(15, 10), (8, 7), (3, 3)])
        itens.append((p, 10, f"Margem líq. {ml:.1f}%"))

    dp = f.get("DIV_PATRIM")
    if _valido(dp) and not financeiro:
        p = _menor_melhor(dp, [(0, 10), (0.5, 8), (1, 6), (2, 3)])
        itens.append((p, 10, f"Dív.Líq/PL {dp:.2f}"))

    dy = f.get("DY")
    if _valido(dy):
        if dy > 20:
            itens.append((0, 10, f"DY anômalo ({dy:.0f}%)"))
        else:
            p = _maior_melhor(dy, [(8, 10), (5, 8), (3, 5), (1, 2)])
            itens.append((p, 10, f"DY {dy:.1f}%"))

    cr = f.get("CRESC_REC")
    if _valido(cr):
        p = _maior_melhor(cr, [(10, 5), (5, 3), (0.0001, 1)])
        itens.append((p, 5, f"Cresc.rec.5a {cr:.1f}%"))

    if len(itens) < 4:
        return np.nan, ["Fundamentos insuficientes"]

    pontos = sum(i[0] for i in itens)
    maximo = sum(i[1] for i in itens)
    score = pontos / maximo * 100
    motivos = [f"{m} (+{p}/{mx})" for p, mx, m in itens]
    return score, motivos


# ==============================================================================
# NOVO: CENÁRIO DE MERCADO (REGIME)
# ==============================================================================

def obter_tendencia_selic():
    """Meta Selic atual e variação nos últimos ~6 meses (API do Banco Central)."""
    try:
        fim = datetime.date.today()
        ini = fim - datetime.timedelta(days=200)
        url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json"
               f"&dataInicial={ini:%d/%m/%Y}&dataFinal={fim:%d/%m/%Y}")
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        dados = r.json()
        if len(dados) < 2:
            return None
        v0 = float(dados[0]["valor"])
        v1 = float(dados[-1]["valor"])
        return v1, v1 - v0
    except Exception:
        return None


def classificar_regime(pontos):
    if pontos >= 4:
        return "RISK-ON"
    if pontos >= 1:
        return "NEUTRO"
    if pontos >= -2:
        return "CAUTELA"
    return "RISK-OFF"


def calcular_regime():
    """
    Mede o 'clima' do mercado e devolve um dicionário com o regime, a pontuação,
    os detalhes e a série do Ibovespa (usada no beta e na força relativa).
    """
    close = baixar_close(INDICE)
    if close is None or len(close) < 220:
        return None

    ema50 = calcular_ema(close, 50)
    ema200 = calcular_ema(close, 200)
    ult = close.iloc[-1]

    estado = {"pts": 0, "det": []}

    def add(p, txt):
        estado["pts"] += p
        estado["det"].append(f"{p:+d}  {txt}")

    if ult > ema200.iloc[-1]:
        add(+2, "Ibovespa acima da EMA200")
    else:
        add(-2, "Ibovespa abaixo da EMA200")

    if ult > ema50.iloc[-1]:
        add(+1, "Ibovespa acima da EMA50")
    else:
        add(-1, "Ibovespa abaixo da EMA50")

    if ema50.iloc[-1] > ema50.iloc[-11]:
        add(+1, "EMA50 do Ibovespa subindo")
    else:
        add(-1, "EMA50 do Ibovespa caindo")

    ret63 = ult / close.iloc[-64] - 1
    if ret63 > 0:
        add(+1, f"Retorno 3 meses {ret63*100:+.1f}%")
    else:
        add(-1, f"Retorno 3 meses {ret63*100:+.1f}%")

    dd = ult / close.tail(252).max() - 1
    if dd > -0.05:
        add(+1, f"Perto da máxima de 12m ({dd*100:.1f}%)")
    elif dd < -0.15:
        add(-1, f"Drawdown de 12m profundo ({dd*100:.1f}%)")

    ret = close.pct_change().dropna()
    v20 = ret.tail(20).std()
    v1y = ret.tail(250).std()
    if v1y > 0 and v20 > 1.5 * v1y:
        add(-1, "Volatilidade de curto prazo elevada")

    usd = baixar_close(DOLAR)
    if usd is not None and len(usd) >= 60:
        ema50_usd = calcular_ema(usd, 50)
        if usd.iloc[-1] < ema50_usd.iloc[-1]:
            add(+1, "Dólar abaixo da EMA50 (real forte)")
        else:
            add(-1, "Dólar acima da EMA50 (real fraco)")

    selic = obter_tendencia_selic()
    if selic is not None:
        nivel, var = selic
        if var <= -0.25:
            add(+1, f"Selic em queda ({nivel:.2f}% a.a., {var:+.2f} p.p. em ~6m)")
        elif var >= 0.25:
            add(-1, f"Selic em alta ({nivel:.2f}% a.a., {var:+.2f} p.p. em ~6m)")
        else:
            add(0, f"Selic estável ({nivel:.2f}% a.a.)")

    pontos = estado["pts"]
    regime = classificar_regime(pontos)
    return {
        "REGIME": regime,
        "PONTOS": pontos,
        "DETALHES": estado["det"],
        "IBOV": close,
        "IBOV_ULT": float(ult),
    }


def metricas_vs_indice(df, ibov_close):
    """Beta (últimos PERIODO_BETA pregões) e força relativa de PERIODO_FR pregões, em p.p."""
    beta = np.nan
    fr = np.nan
    try:
        juntos = pd.concat([df["Close"].pct_change(), ibov_close.pct_change()],
                           axis=1, join="inner").dropna().tail(PERIODO_BETA)
        juntos.columns = ["a", "i"]
        if len(juntos) >= 60 and juntos["i"].var() > 0:
            beta = juntos["a"].cov(juntos["i"]) / juntos["i"].var()

        comum = df["Close"].index.intersection(ibov_close.index)
        if len(comum) > PERIODO_FR + 1:
            a = df["Close"].loc[comum]
            i = ibov_close.loc[comum]
            ra = a.iloc[-1] / a.iloc[-PERIODO_FR - 1] - 1
            ri = i.iloc[-1] / i.iloc[-PERIODO_FR - 1] - 1
            fr = (ra - ri) * 100
    except Exception:
        pass
    return float(beta) if _valido(beta) else np.nan, float(fr) if _valido(fr) else np.nan


def score_cenario(beta, fr, regime):
    """Ajuste (em pontos) por força relativa e beta, conforme o regime."""
    ajuste = 0
    motivos = []

    if _valido(fr):
        if fr > 5:
            ajuste += 4
            motivos.append(f"Força relativa {PERIODO_FR}d: {fr:+.1f} p.p. vs Ibov (+4)")
        elif fr > 0:
            ajuste += 2
            motivos.append(f"Força relativa {PERIODO_FR}d: {fr:+.1f} p.p. vs Ibov (+2)")
        elif fr <= -5:
            ajuste -= 4
            motivos.append(f"Força relativa {PERIODO_FR}d: {fr:+.1f} p.p. vs Ibov (-4)")
        else:
            motivos.append(f"Força relativa {PERIODO_FR}d: {fr:+.1f} p.p. vs Ibov (0)")

    if _valido(beta):
        if regime == "RISK-ON" and beta >= 1.1:
            ajuste += 2
            motivos.append(f"Beta {beta:.2f} favorece em mercado de alta (+2)")
        elif regime in ("CAUTELA", "RISK-OFF"):
            if beta > 1.3:
                ajuste -= 4
                motivos.append(f"Beta {beta:.2f} alto em mercado defensivo (-4)")
            elif beta < 0.8:
                ajuste += 4
                motivos.append(f"Beta {beta:.2f} defensivo (+4)")

    return ajuste, motivos


# ==============================================================================
# NOVO (V3.1): PULLBACK, FIBONACCI, PLANO DA OPERAÇÃO E HISTÓRICO DO SETUP
# ==============================================================================

def calcular_atr(df, periodo=PERIODO_ATR):
    """Oscilação média diária (Average True Range)."""
    close = df["Close"]
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - close.shift(1)).abs(),
                    (df["Low"] - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / periodo, adjust=False).mean()


def _num(x, casas=2):
    return float(round(float(x), casas))


def calcular_fibonacci(df):
    """
    Perna de alta mais recente: a máxima dos últimos MAX_IDADE_ALTO pregões e a
    menor mínima que a antecede dentro de JANELA_SWING pregões. O recuo (retr) é
    a fração dessa perna que o preço já devolveu: 0 = na máxima, 1 = na mínima.
    Retorna None quando não há perna de alta relevante.
    """
    janela = df.tail(JANELA_SWING)
    if len(janela) < 40:
        return None
    recente = janela.tail(MAX_IDADE_ALTO)
    alto = float(recente["High"].max())
    data_alto = recente["High"].idxmax()
    antes = janela.loc[:data_alto].tail(MAX_PERNA + 1)
    baixo = float(antes["Low"].min())
    data_baixo = antes["Low"].idxmin()
    amplitude = alto - baixo
    if baixo <= 0 or amplitude / baixo < AMPLITUDE_MIN:
        return None

    close = float(df["Close"].iloc[-1])
    return {
        "alto": _num(alto),
        "baixo": _num(baixo),
        "data_alto": data_alto.strftime("%Y-%m-%d"),
        "data_baixo": data_baixo.strftime("%Y-%m-%d"),
        "idade_alto": int(len(df) - 1 - df.index.get_loc(data_alto)),
        "amplitude_pct": _num(amplitude / baixo * 100, 1),
        "retr": float(round((alto - close) / amplitude, 4)),
        "niveis": {f"{k * 100:.1f}": _num(alto - k * amplitude) for k in FIB_NIVEIS},
    }


def identificar_candle_alta(df):
    """Martelo ou engolfo de alta no último candle."""
    if len(df) < 2:
        return None
    a, p = df.iloc[-1], df.iloc[-2]
    rng = float(a["High"] - a["Low"])
    if rng <= 0:
        return None
    corpo = abs(float(a["Close"] - a["Open"]))
    base = max(corpo, 0.1 * rng)
    sombra_inf = float(min(a["Open"], a["Close"]) - a["Low"])
    sombra_sup = float(a["High"] - max(a["Open"], a["Close"]))
    if sombra_inf >= 2 * base and sombra_sup <= base and (a["Close"] - a["Low"]) / rng >= 0.6:
        return "martelo"
    if p["Close"] < p["Open"] and a["Close"] > a["Open"] and a["Close"] >= p["Open"] and a["Open"] <= p["Close"]:
        return "engolfo de alta"
    return None


def avaliar_pullback(df, fib, fr):
    """
    Estágio do pullback e nota de qualidade (0-100).
    Estágios: SEM PULLBACK, ROMPEU, EM CORREÇÃO, NO SUPORTE, RETOMANDO.
    """
    atual, ant = df.iloc[-1], df.iloc[-2]
    close = float(atual["Close"])
    retr = fib["retr"] if fib else None
    contexto = bool(atual["EMA50"] > atual["EMA200"] and close > atual["EMA200"])

    ult5 = df.tail(5)
    tocou = bool((((ult5["Low"] - ult5["EMA50"]) / ult5["EMA50"]).abs() <= TOLERANCIA_EMA50).any())
    na_zona = bool(fib and (FIB_ZONA[0] - 0.02) <= retr <= (FIB_ZONA[1] + 0.02))
    candle = identificar_candle_alta(df)
    rompeu_max = bool(close > float(ant["High"]))
    acima_ema9 = bool(close > float(atual["EMA9"]) and close > float(ant["Close"]))
    confirmou = bool(candle or rompeu_max or acima_ema9)

    nivel = None
    if fib:
        k = min(FIB_NIVEIS, key=lambda x: abs(retr - x))
        nivel = f"{k * 100:.1f}%".replace(".", ",")

    motivos = []
    if not contexto:
        estagio = "SEM PULLBACK"
        motivos.append("Fora de tendência de alta (exige EMA50 > EMA200 e preço > EMA200)")
    elif fib is not None and (retr > FIB_MAX_RETR or close < fib["baixo"]):
        estagio = "ROMPEU"
        motivos.append(f"Recuo de {retr * 100:.0f}% passou de 78,6%: a perna de alta foi perdida")
    elif fib is None and not tocou:
        estagio = "SEM PULLBACK"
        motivos.append("Sem perna de alta recente e sem toque na EMA50")
    elif fib is not None and retr < 0.15 and not tocou:
        estagio = "SEM PULLBACK"
        motivos.append(f"Preço perto da máxima (recuo de {retr * 100:.0f}%)")
    else:
        suporte = na_zona or tocou
        if suporte and confirmou and close > float(ant["Close"]):
            estagio = "RETOMANDO"
        elif suporte:
            estagio = "NO SUPORTE"
        else:
            estagio = "EM CORREÇÃO"

    if estagio in ("SEM PULLBACK", "ROMPEU"):
        return {"estagio": estagio, "pb_score": None, "nivel": nivel,
                "candle": candle, "motivos": motivos}

    pts = 0

    # contexto de tendência (20)
    ctx = 0
    ctx += 8 if atual["EMA50"] > atual["EMA200"] else 0
    ctx += 4 if atual["EMA200_UP"] else 0
    ctx += 4 if close > atual["EMA200"] else 0
    ctx += 4 if atual["EMA50_UP"] else 0
    pts += ctx
    motivos.append(f"Tendência de alta: {ctx}/20")

    # força relativa contra o Ibovespa (5)
    if _valido(fr):
        if fr > 0:
            pts += 5
            motivos.append(f"Mais forte que o Ibovespa em {PERIODO_FR} pregões ({fr:+.1f} p.p.)")
        elif fr > -3:
            pts += 2

    # profundidade do recuo (25)
    if fib is not None:
        pr = f"{retr * 100:.0f}%"
        if 0.362 <= retr <= 0.638:
            pts += 25
            motivos.append(f"Recuo de {pr} da perna de alta: dentro da zona de 38,2% a 61,8%")
        elif 0.216 <= retr < 0.362 or 0.638 < retr <= FIB_MAX_RETR:
            pts += 15
            motivos.append(f"Recuo de {pr}: fora da zona ideal, mas ainda válido")
        elif retr >= 0.15:
            pts += 8
            motivos.append(f"Recuo raso de {pr}")
    elif tocou:
        pts += 8
        motivos.append("Toque na EMA50 sem perna de alta clara para medir o recuo")

    # volume durante o recuo (15)
    vol_media = float(atual["VOL_MEDIA"]) if _valido(atual["VOL_MEDIA"]) else 0.0
    if vol_media > 0:
        v5 = float(df["Volume"].tail(5).mean()) / vol_media
        if v5 <= 0.8:
            pts += 15
            motivos.append(f"Volume secando no recuo ({v5:.2f}x da média): vendedores fracos")
        elif v5 <= 1.0:
            pts += 10
            motivos.append(f"Volume abaixo da média no recuo ({v5:.2f}x)")
        elif v5 <= 1.2:
            pts += 4
        else:
            motivos.append(f"Volume alto no recuo ({v5:.2f}x): atenção a venda forte")

    # RSI reiniciado (15)
    rsi = float(atual["RSI"])
    subindo = bool(atual["RSI_UP"])
    if 40 <= rsi <= 50 and subindo:
        pts += 15
        motivos.append(f"RSI reiniciado e subindo ({rsi:.0f})")
    elif 40 <= rsi <= 55:
        pts += 10
        motivos.append(f"RSI em zona de repouso ({rsi:.0f})")
    elif 55 < rsi <= 60:
        pts += 6
    elif rsi < 40:
        pts += 3
        motivos.append(f"RSI fraco ({rsi:.0f})")

    # confirmação do candle (20)
    conf = 0
    if candle:
        conf += 10
        motivos.append(f"Candle de {candle}")
    if rompeu_max:
        conf += 6
        motivos.append("Fechou acima da máxima do dia anterior")
    if close > float(atual["EMA9"]):
        conf += 4
    pts += conf

    return {"estagio": estagio, "pb_score": int(min(100, pts)), "nivel": nivel,
            "candle": candle, "motivos": motivos}


def montar_plano(df, fib):
    """
    Plano da operação (regra, não recomendação):
      entrada  = preço atual
      stop     = abaixo da menor mínima de 10 pregões e do nível de Fibonacci imediatamente
                 abaixo do preço (o que for mais baixo), menos 0,5 ATR
      alvo 1   = máxima anterior da perna de alta (ou 2x o risco, sem perna)
      alvo 2   = extensão de 127,2% da perna (ou 3x o risco)
    """
    atual = df.iloc[-1]
    entrada = float(atual["Close"])
    atr = float(atual["ATR"])
    if not _valido(atr) or atr <= 0:
        return None

    stop = float(df["Low"].tail(10).min())
    if fib:
        abaixo = [p for p in fib["niveis"].values() if p < entrada]
        if abaixo:
            stop = min(stop, max(abaixo))
    stop -= 0.5 * atr
    if stop >= entrada:
        return None

    risco = entrada - stop
    risco_pct = risco / entrada * 100
    if fib and fib["alto"] > entrada * 1.01:
        alvo = fib["alto"]
        alvo_tipo = "máxima anterior"
        alvo2 = fib["alto"] + 0.272 * (fib["alto"] - fib["baixo"])
    else:
        alvo = entrada + 2 * risco
        alvo_tipo = "2 × risco"
        alvo2 = entrada + 3 * risco

    return {
        "entrada": _num(entrada),
        "stop": _num(stop),
        "alvo": _num(alvo),
        "alvo2": _num(alvo2),
        "alvo_tipo": alvo_tipo,
        "risco_pct": _num(risco_pct, 1),
        "ganho_pct": _num((alvo - entrada) / entrada * 100, 1),
        "ganho2_pct": _num((alvo2 - entrada) / entrada * 100, 1),
        "rr": _num((alvo - entrada) / risco),
        "valido": bool(risco_pct <= STOP_MAX_PCT),
    }


def historico_pullback(df):
    """
    Como esta ação se comportou nos pullbacks passados à EMA50 (mesma regra do
    seu identificar_pullback). Para cada ocorrência: entrada no fechamento, stop
    abaixo da menor mínima de 10 pregões menos 0,5 ATR, alvo de 2x o risco,
    prazo de HIST_HORIZONTE pregões. Stop e alvo no mesmo dia contam como stop.
    Só entram ocorrências que tiveram o prazo completo. Usa apenas dados até o
    dia de cada sinal para decidir a entrada.
    """
    n = len(df)
    if n < 260:
        return None
    close = df["Close"].values
    low = df["Low"].values
    high = df["High"].values
    e50 = df["EMA50"].values
    e200 = df["EMA200"].values
    atr = df["ATR"].values

    resultados, neutros = [], 0
    i = 210                                  # depois do aquecimento da EMA200
    while i < n - 1:
        j = slice(i - 9, i + 1)
        if not ((e50[j] > e200[j]).mean() >= 0.70 and close[i] > e200[i]):
            i += 1
            continue
        toque = bool((np.abs(low[j] - e50[j]) / e50[j] <= TOLERANCIA_EMA50).any())
        if not (toque and close[i] > e50[i] and close[i] > close[i - 1]):
            i += 1
            continue

        entrada = close[i]
        stop = low[j].min() - 0.5 * atr[i]
        risco = entrada - stop
        fim = i + HIST_HORIZONTE
        if risco > 0 and risco / entrada * 100 <= STOP_MAX_PCT and fim <= n - 1:
            alvo = entrada + HIST_ALVO_R * risco
            res = None
            for k in range(i + 1, fim + 1):
                if low[k] <= stop:
                    res = -1.0
                    break
                if high[k] >= alvo:
                    res = HIST_ALVO_R
                    break
            if res is None:
                neutros += 1
            else:
                resultados.append(res)
        i += 10                              # evita contar o mesmo pullback várias vezes

    if not resultados:
        return None
    total = len(resultados)
    acertos = sum(1 for r in resultados if r > 0)
    return {
        "n": total,
        "acertos": acertos,
        "taxa": _num(acertos / total * 100, 0),
        "esperanca_r": _num(sum(resultados) / total),
        "neutros": neutros,
        "amostra_pequena": bool(total < 8),
    }


def avaliar_oportunidade(estagio, pb_score, plano, tendencia, close, ema200, score_val, regime_nome):
    """Oportunidade = pullback no suporte + boa qualidade + ganho/risco suficiente."""
    rr_min = RR_MIN_DEFENSIVO if regime_nome == "RISK-OFF" else RR_MIN
    ok_notas, falta = [], []

    if estagio in ("NO SUPORTE", "RETOMANDO"):
        ok_notas.append(f"Estágio: {estagio.lower()}")
    elif estagio == "EM CORREÇÃO":
        falta.append("Ainda em correção: esperar chegar a um suporte")
    else:
        falta.append(f"Estágio {estagio.lower()}: não há pullback aproveitável")

    if pb_score is not None and pb_score >= PB_MIN:
        ok_notas.append(f"Qualidade do pullback {pb_score}/100")
    elif pb_score is not None:
        falta.append(f"Qualidade do pullback {pb_score}/100, abaixo do mínimo de {PB_MIN}")

    if plano is None:
        falta.append("Não foi possível montar o plano (stop inválido)")
    else:
        if not plano["valido"]:
            falta.append(f"Stop distante demais ({plano['risco_pct']:.1f}% do preço; máximo {STOP_MAX_PCT:.0f}%)")
        if plano["rr"] >= rr_min:
            ok_notas.append(f"Ganho/risco de {plano['rr']:.1f} para 1")
        else:
            falta.append(f"Ganho/risco de {plano['rr']:.1f} para 1, abaixo do mínimo de {rr_min:.0f} para 1")

    if tendencia == "BAIXA" or close <= ema200:
        falta.append("Tendência de baixa ou preço abaixo da EMA200")

    if _valido(score_val) and score_val < VALOR_MIN:
        falta.append(f"Nota de valor baixa ({score_val:.0f}, mínimo {VALOR_MIN})")

    if regime_nome == "RISK-OFF":
        ok_notas.append("Mercado defensivo: exigido ganho/risco de pelo menos 3 para 1")

    ok = not falta
    return ok, (ok_notas if ok else [f"Falta: {f}" for f in falta])


# ==============================================================================
# V3.2: REVERSÃO, CHECKLIST, LÍDERES DE FORÇA RELATIVA, PROJEÇÃO E TERMÔMETRO
# ==============================================================================

REV_JANELA_ALTO = 60            # a queda é medida a partir da máxima destes últimos pregões
REV_QUEDA_MIN = 15.0            # queda mínima (%) para a ação entrar no radar de reversão
REV_RSI_SOBREVENDA = 30
REV_VOL_PICO = 1.8              # volume relativo que marca capitulação
REV_ATR_EXPANSAO = 1.3          # ATR% atual / mediana de 120 pregões
REV_DIST_EMA50 = -8.0           # distância (%) abaixo da EMA50 que marca esticamento
REV_FUNDO_MIN_IDADE = 3         # pregões desde a mínima para considerar que o fundo "segurou"
REV_FUNDO_MAX_IDADE = 25
REV_RECUP_MAX = 0.618           # acima disso a queda já foi recuperada: tarde para o radar
REV_ALVO_MIN_PCT = 4.0          # alvo 1 precisa estar a pelo menos 4% do preço
REV_RR_MIN = 2.0
REV_RR_MIN_DEFENSIVO = 3.0
LIDER_FR_MIN = 3.0              # p.p. de força relativa (60 pregões) contra o Ibovespa
LIDER_FR20_MIN = 2.0            # idem, 20 pregões
LIDER_DD_MAX = 10.0             # no máximo 10% abaixo da máxima de 60 pregões
PROJ_HORIZONTES = (10, 20, 30)
PROJ_JANELA = 750               # pregões de histórico usados na projeção

ESTAGIOS_REV_RADAR = ("ESTABILIZANDO", "CONFIRMANDO", "CONFIRMADA")


def _f(x, casas=2):
    """float arredondado, ou None se NaN/inexistente (seguro para JSON)."""
    try:
        if x is None or pd.isna(x):
            return None
        return float(round(float(x), casas))
    except Exception:
        return None


def avaliar_reversao(df):
    """
    Reversão em três etapas: CAPITULAÇÃO (queda esticada, volume e volatilidade em pico),
    ESTABILIZAÇÃO (o fundo segura, sem nova mínima) e CONFIRMAÇÃO (médias curtas, MACD e volume).
    Estágios: SEM QUEDA, EM QUEDA, CAPITULAÇÃO, ESTABILIZANDO, CONFIRMANDO, CONFIRMADA, RECUPERADA.
    Aceita df sem volume (usado no Ibovespa): as verificações de volume são ignoradas.
    """
    base = {"estagio": "SEM QUEDA", "score": None, "sinais": [], "queda_pct": None, "fundo": None,
            "alto": None, "idade_fundo": None, "recuperado_pct": None, "n_conf": 0, "n_cap": 0}
    if len(df) < 80:
        return base

    ult = df.iloc[-1]
    alto = float(df.tail(REV_JANELA_ALTO + 1)["High"].max())
    jan = df.tail(REV_FUNDO_MAX_IDADE + 1)
    fundo = float(jan["Low"].min())
    pos_fundo = int(np.argmin(jan["Low"].values))
    idade = len(jan) - 1 - pos_fundo
    close = float(ult["Close"])
    amp = alto - fundo
    if alto <= 0 or amp <= 0:
        return base
    queda = amp / alto * 100
    # janela de capitulação: em torno do fundo (até 30 pregões), e não só os últimos 10
    ult10 = df.tail(min(30, max(10, idade + 6)))
    rsi_min = float(ult10["RSI"].min())

    contexto = queda >= REV_QUEDA_MIN or (rsi_min < REV_RSI_SOBREVENDA and close < float(ult["EMA50"]))
    base.update({"queda_pct": _f(queda, 1), "fundo": _f(fundo), "alto": _f(alto), "idade_fundo": idade})
    if not contexto:
        return base

    recup = (close - fundo) / amp
    base["recuperado_pct"] = _f(recup * 100, 0)

    # ---- capitulação (últimos 10 pregões)
    sinais, pts, tot = [], 0.0, 0.0

    def item(grupo, texto, ok, peso):
        nonlocal pts, tot
        if ok is None:
            return
        sinais.append({"grupo": grupo, "texto": texto, "ok": bool(ok)})
        tot += peso
        pts += peso if ok else 0

    dist_min = float(ult10["DIST_EMA50"].min())
    vol_max = ult10["VOL_REL"].max()
    atr_pct = df["ATR_PCT"]
    atr_ref = float(atr_pct.tail(120).median()) if len(atr_pct) >= 120 else np.nan
    atr_pico = float(ult10["ATR_PCT"].max())

    item("Capitulação", f"RSI abaixo de {REV_RSI_SOBREVENDA} perto do fundo (mín. {rsi_min:.0f})",
         rsi_min < REV_RSI_SOBREVENDA, 6)
    item("Capitulação", "Volume em pico (acima de 1,8x a média)",
         None if pd.isna(vol_max) else bool(vol_max >= REV_VOL_PICO), 5)
    item("Capitulação", "Volatilidade em expansão (ATR acima de 1,3x o normal)",
         None if (pd.isna(atr_ref) or atr_ref <= 0) else bool(atr_pico / atr_ref >= REV_ATR_EXPANSAO), 4)
    item("Capitulação", f"Preço esticado abaixo da EMA50 (até {dist_min:.0f}%)",
         bool(dist_min <= REV_DIST_EMA50), 5)
    n_cap = sum(1 for s in sinais if s["grupo"] == "Capitulação" and s["ok"])

    # ---- estabilização
    fundo_seguro = idade >= REV_FUNDO_MIN_IDADE
    item("Estabilização", f"Fundo segurou há {idade} pregões, sem nova mínima", fundo_seguro, 8)
    item("Estabilização", "Recuperou pelo menos 3% desde o fundo", bool(close / fundo - 1 >= 0.03), 8)

    anterior = df.iloc[max(0, len(df) - len(jan) - 45): max(0, len(df) - len(jan) - 5)]
    ok_div = None
    txt_div = "Fundo mais alto, duplo fundo ou divergência de alta no RSI"
    if len(anterior) >= 10:
        pos_ant = int(np.argmin(anterior["Low"].values))
        low_ant = float(anterior["Low"].iloc[pos_ant])
        rsi_ant = float(anterior["RSI"].iloc[pos_ant])
        rsi_fundo = float(jan["RSI"].iloc[pos_fundo])
        if fundo >= low_ant * 0.98:
            ok_div = True
            txt_div = "Fundo mais alto ou duplo fundo em relação ao anterior"
        elif rsi_fundo > rsi_ant:
            ok_div = True
            txt_div = "Divergência de alta no RSI (preço fez mínima menor, RSI não)"
        else:
            ok_div = False
    item("Estabilização", txt_div, ok_div, 12)

    # ---- confirmação
    ant, ant2 = df.iloc[-2], df.iloc[-3]
    hist_sobe = bool(ult["MACD_HIST"] > ant["MACD_HIST"] > ant2["MACD_HIST"])
    c = [
        ("Fechou acima da EMA9", close > float(ult["EMA9"]), 8),
        ("Fechou acima da EMA21", close > float(ult["EMA21"]), 8),
        ("Histograma do MACD subindo há 2 pregões", hist_sobe, 12),
        ("MACD acima da linha de sinal", bool(ult["MACD"] > ult["MACD_SIGNAL"]), 8),
    ]
    ult5 = df.tail(5)
    altas = ult5[ult5["Close"] > ult5["Close"].shift(1).fillna(ult5["Close"])]
    vol_alta = altas["VOL_REL"].mean() if len(altas) else np.nan
    c.append(("Volume nos dias de alta acima da média",
              None if pd.isna(vol_alta) else bool(vol_alta >= 1.0), 8))
    for texto, ok, peso in c:
        item("Confirmação", texto, ok, peso)
    item("Confirmação", "Recuperou a EMA50", bool(close > float(ult["EMA50"])), 8)
    n_conf = sum(1 for s in sinais if s["grupo"] == "Confirmação" and s["ok"] and s["texto"] != "Recuperou a EMA50")

    higher_low = bool(float(df.tail(3)["Low"].min()) > fundo * 1.003)
    if recup > REV_RECUP_MAX:
        estagio = "RECUPERADA"
    elif not fundo_seguro:
        estagio = "CAPITULAÇÃO" if n_cap >= 2 else "EM QUEDA"
    elif n_conf >= 4 and higher_low:
        estagio = "CONFIRMADA"
    elif n_conf >= 3:
        estagio = "CONFIRMANDO"
    else:
        estagio = "ESTABILIZANDO"

    score = int(round(pts / tot * 100)) if tot > 0 else None
    base.update({"estagio": estagio, "score": score, "sinais": sinais, "n_conf": n_conf, "n_cap": n_cap})
    return base


def montar_plano_reversao(df, rev):
    """Plano da reversão: stop abaixo do fundo; alvos nos níveis de recuperação da queda."""
    if rev["estagio"] not in ESTAGIOS_REV_RADAR or rev["fundo"] is None:
        return None
    ult = df.iloc[-1]
    close = float(ult["Close"])
    atr = float(ult["ATR"]) if _valido(ult["ATR"]) else 0.0
    fundo, alto = rev["fundo"], rev["alto"]
    # stop abaixo da menor mínima dos últimos 8 pregões (nunca abaixo do fundo menos 0,5 ATR)
    stop = max(float(df.tail(8)["Low"].min()), fundo) - 0.5 * atr
    risco = close - stop
    amp = alto - fundo
    if risco <= 0 or amp <= 0:
        return None
    niveis = [(0.382, "38,2% da queda"), (0.5, "50% da queda"), (0.618, "61,8% da queda"),
              (0.786, "78,6% da queda"), (1.0, "máxima anterior")]
    cand = [(fundo + k * amp, nome) for k, nome in niveis if fundo + k * amp >= close * (1 + REV_ALVO_MIN_PCT / 100)]
    if not cand:
        return None
    alvo, tipo = cand[0]
    alvo2 = cand[1][0] if len(cand) > 1 else alvo + 0.272 * amp
    risco_pct = risco / close * 100
    return {
        "entrada": _f(close), "stop": _f(stop), "alvo": _f(alvo), "alvo2": _f(alvo2), "alvo_tipo": tipo,
        "risco_pct": _f(risco_pct, 1), "ganho_pct": _f((alvo / close - 1) * 100, 1),
        "ganho2_pct": _f((alvo2 / close - 1) * 100, 1), "rr": _f((alvo - close) / risco, 2),
        "valido": bool(risco_pct <= STOP_MAX_PCT),
    }


def avaliar_reversao_op(rev, plano, regime_nome):
    """Reversão aproveitável: confirmando/confirmada, plano válido e ganho/risco suficiente."""
    rr_min = REV_RR_MIN_DEFENSIVO if regime_nome in ("RISK-OFF", "CAUTELA") else REV_RR_MIN
    falta, ok_n = [], []
    if rev["estagio"] in ("CONFIRMANDO", "CONFIRMADA"):
        ok_n.append(f"Reversão {rev['estagio'].lower()} ({rev['n_conf']} de 5 confirmações)")
    elif rev["estagio"] == "ESTABILIZANDO":
        falta.append("Fundo ainda sem confirmação: manter em observação")
    elif rev["estagio"] in ("EM QUEDA", "CAPITULAÇÃO"):
        falta.append("Ainda fazendo mínimas: esperar o fundo segurar")
    elif rev["estagio"] == "RECUPERADA":
        falta.append("Queda já recuperada em boa parte: tarde para a reversão")
    else:
        falta.append("Sem queda relevante para reverter")
    if plano is None:
        falta.append("Sem plano calculável")
    else:
        if not plano["valido"]:
            falta.append(f"Stop distante demais ({plano['risco_pct']:.1f}% do preço)")
        if plano["rr"] >= rr_min:
            ok_n.append(f"Ganho/risco de {plano['rr']:.1f} para 1")
        else:
            falta.append(f"Ganho/risco de {plano['rr']:.1f} para 1, abaixo do mínimo de {rr_min:.0f} para 1")
    ok = not falta
    return ok, (ok_n if ok else [f"Falta: {f}" for f in falta])


def avaliar_lider(df, ibov_close, tendencia, fr):
    """Líder de força relativa: segura preço e supera o Ibovespa quando o mercado cai."""
    if ibov_close is None or not _valido(fr):
        return None
    atual = df.iloc[-1]
    close = float(atual["Close"])
    fr20 = np.nan
    try:
        comum = df["Close"].index.intersection(ibov_close.index)
        if len(comum) > 21:
            a, i = df["Close"].loc[comum], ibov_close.loc[comum]
            fr20 = ((a.iloc[-1] / a.iloc[-21]) - (i.iloc[-1] / i.iloc[-21])) * 100
    except Exception:
        pass
    alto60 = float(df.tail(REV_JANELA_ALTO + 1)["High"].max())
    dd = (alto60 - close) / alto60 * 100
    cond = [
        (f"Força relativa de {fr:+.1f} p.p. em 60 pregões (mín. {LIDER_FR_MIN:.0f})", fr >= LIDER_FR_MIN),
        (f"Força relativa de {fr20:+.1f} p.p. em 20 pregões (mín. {LIDER_FR20_MIN:.0f})",
         _valido(fr20) and fr20 >= LIDER_FR20_MIN),
        ("Preço acima da EMA200", close > float(atual["EMA200"])),
        ("Preço acima da EMA50", close > float(atual["EMA50"])),
        (f"A {dd:.1f}% da máxima de 60 pregões (máx. {LIDER_DD_MAX:.0f}%)", dd <= LIDER_DD_MAX),
        ("Tendência diferente de baixa", tendencia != "BAIXA"),
    ]
    ok = all(c[1] for c in cond)
    score = max(0, min(100, 40 + 2 * fr + (2 * fr20 if _valido(fr20) else 0)))
    return {"ok": bool(ok), "score": int(round(score)), "fr20": _f(fr20, 1), "dd60": _f(dd, 1),
            "notas": [("OK: " if c[1] else "Falta: ") + c[0] for c in cond]}


def montar_checklist(df, av, fib):
    """Condições do setup de pullback em tendência de alta, visíveis uma a uma."""
    a, p = df.iloc[-1], df.iloc[-2]
    close = float(a["Close"])
    vol5 = df.tail(5)["VOL_REL"].mean()
    itens = [
        ("Tendência", "Preço acima da EMA200", close > float(a["EMA200"])),
        ("Tendência", "EMA50 acima da EMA200", bool(a["EMA50"] > a["EMA200"])),
        ("Tendência", "EMA200 subindo (30 pregões)", bool(a["EMA200_UP"])),
        ("Tendência", "EMA50 subindo (10 pregões)", bool(a["EMA50_UP"])),
        ("Pullback", "Recuo na zona 38,2% a 61,8% de Fibonacci",
         bool(av["estagio"] in ("NO SUPORTE", "RETOMANDO") and fib is not None)),
        ("Pullback", "Volume secando durante o recuo", None if pd.isna(vol5) else bool(vol5 < 1.0)),
        ("Pullback", "RSI em zona de repouso (35 a 55)", bool(35 <= float(a["RSI"]) <= 55)),
        ("MACD", "MACD acima da linha de sinal", bool(a["MACD"] > a["MACD_SIGNAL"])),
        ("MACD", "Histograma do MACD subindo", bool(a["MACD_HIST"] > p["MACD_HIST"])),
        ("MACD", "MACD acima de zero", bool(a["MACD"] > 0)),
        ("Gatilho", "Candle de confirmação (martelo ou engolfo)", bool(av["candle"])),
        ("Gatilho", "Fechou acima da EMA9", close > float(a["EMA9"])),
    ]
    out = [{"grupo": g, "texto": t, "ok": bool(o)} for g, t, o in itens if o is not None]
    macd_gatilho = bool(a["MACD_HIST"] > p["MACD_HIST"] and
                        (a["MACD"] > a["MACD_SIGNAL"] or bool(df.tail(3)["CROSS_MACD"].any())))
    return out, macd_gatilho


def projecao_cenarios(df, plano):
    """
    Faixas de preço em 10, 20 e 30 pregões (percentis 10, 50 e 90 dos retornos reais do papel)
    e, com plano, a frequência histórica de alvo/stop com a mesma geometria entrando em qualquer dia.
    É base histórica, não previsão.
    """
    close = df["Close"]
    ult = float(close.iloc[-1])
    out = {"horizontes": [], "base": None, "janela": int(min(len(close), PROJ_JANELA))}
    logc = np.log(close.astype(float))
    for h in PROJ_HORIZONTES:
        lr = logc.diff(h).dropna().tail(PROJ_JANELA)
        if len(lr) < 120:
            continue
        p10, p50, p90 = np.percentile(lr.values, [10, 50, 90])
        out["horizontes"].append({
            "dias": h,
            "p10": _f(ult * np.exp(p10)), "p50": _f(ult * np.exp(p50)), "p90": _f(ult * np.exp(p90)),
            "p10_pct": _f((np.exp(p10) - 1) * 100, 1), "p50_pct": _f((np.exp(p50) - 1) * 100, 1),
            "p90_pct": _f((np.exp(p90) - 1) * 100, 1),
        })
    if plano and plano.get("risco_pct") and plano["risco_pct"] > 0:
        H = HIST_HORIZONTE
        c = close.values.astype(float)[-PROJ_JANELA - H:]
        hi = df["High"].values.astype(float)[-PROJ_JANELA - H:]
        lo = df["Low"].values.astype(float)[-PROJ_JANELA - H:]
        if len(c) > H + 60:
            n = len(c) - H
            ent = c[:n]
            idx = np.arange(1, H + 1)[None, :] + np.arange(n)[:, None]
            jh, jl = hi[idx], lo[idx]
            stop_l = (ent * (1 - plano["risco_pct"] / 100))[:, None]
            r = {}
            primeiro_stop = np.where((jl <= stop_l).any(axis=1), (jl <= stop_l).argmax(axis=1), H + 1)
            for nome, g in (("alvo1", plano["ganho_pct"]), ("alvo2", plano["ganho2_pct"])):
                alvo_l = (ent * (1 + g / 100))[:, None]
                primeiro_alvo = np.where((jh >= alvo_l).any(axis=1), (jh >= alvo_l).argmax(axis=1), H + 1)
                r[nome] = float(np.mean(primeiro_alvo < primeiro_stop) * 100)
            p_stop = float(np.mean(primeiro_stop <= H) * 100)
            out["base"] = {"n": int(n), "horizonte": H, "p_alvo1": _f(r["alvo1"], 0), "p_alvo2": _f(r["alvo2"], 0),
                           "p_stop": _f(p_stop, 0)}
    return out


def termometro_mercado(regime, resultados):
    """Amplitude do mercado e fase (queda aberta, fundo em formação, reversão em curso, tendência saudável)."""
    dfs = [df for df in HIST_CACHE.values() if len(df) >= 210]
    n = len(dfs)
    if n < 10:
        return None

    def pct(cond):
        return float(round(sum(1 for d in dfs if cond(d)) / n * 100, 1))

    acima50 = pct(lambda d: d["Close"].iloc[-1] > d["EMA50"].iloc[-1])
    acima200 = pct(lambda d: d["Close"].iloc[-1] > d["EMA200"].iloc[-1])
    acima50_5d = pct(lambda d: d["Close"].iloc[-6] > d["EMA50"].iloc[-6])
    acima50_20d = pct(lambda d: d["Close"].iloc[-21] > d["EMA50"].iloc[-21])
    macd_pos = pct(lambda d: d["MACD"].iloc[-1] > d["MACD_SIGNAL"].iloc[-1])
    sobrevend = pct(lambda d: d["RSI"].iloc[-1] < REV_RSI_SOBREVENDA)
    min20 = sum(1 for d in dfs if d["Close"].iloc[-1] <= d["Close"].iloc[-21:-1].min())
    max20 = sum(1 for d in dfs if d["Close"].iloc[-1] >= d["Close"].iloc[-21:-1].max())

    est = {}
    for r in resultados:
        e = (r.get("REV") or {}).get("estagio")
        if e:
            est[e] = est.get(e, 0) + 1
    n_conf = est.get("CONFIRMANDO", 0) + est.get("CONFIRMADA", 0)
    n_estab = est.get("ESTABILIZANDO", 0)
    n_queda = est.get("EM QUEDA", 0) + est.get("CAPITULAÇÃO", 0)
    total_res = max(1, len(resultados))

    ibov = None
    ibov_dd = None
    if regime is not None and regime.get("IBOV") is not None:
        c = regime["IBOV"].astype(float).dropna()
        d = pd.DataFrame({"Close": c})
        d["Open"] = c.shift(1).fillna(c)
        d["High"] = np.maximum(c, c.shift(1).fillna(c))
        d["Low"] = np.minimum(c, c.shift(1).fillna(c))
        d["Volume"] = np.nan
        d = calcular_indicadores(d)
        d = d.dropna(subset=["Close", "EMA9", "EMA21", "EMA50", "EMA200", "RSI", "MACD", "MACD_SIGNAL", "MACD_HIST", "ATR"])
        if len(d) > 100:
            ibov = avaliar_reversao(d)
            ibov_dd = _f((float(d["Close"].iloc[-1]) / float(d["Close"].tail(252).max()) - 1) * 100, 1)

    est_ibov = ibov["estagio"] if ibov else None
    delta5 = round(acima50 - acima50_5d, 1)
    if acima50 >= 60 and (est_ibov in (None, "SEM QUEDA", "RECUPERADA")):
        fase = "TENDÊNCIA SAUDÁVEL"
    elif est_ibov in ("CONFIRMANDO", "CONFIRMADA") or (n_conf / total_res >= 0.15 and delta5 > 0):
        fase = "REVERSÃO EM CURSO"
    elif est_ibov == "ESTABILIZANDO" or sobrevend >= 20 or (n_estab + n_conf) / total_res >= 0.15:
        fase = "FUNDO EM FORMAÇÃO"
    elif est_ibov in ("EM QUEDA", "CAPITULAÇÃO") or acima50 < 35:
        fase = "QUEDA ABERTA"
    else:
        fase = "NEUTRO"

    leitura = [
        f"{acima50:.0f}% das ações acima da EMA50 ({delta5:+.0f} p.p. em 5 pregões) e {acima200:.0f}% acima da EMA200",
        f"{min20} ações em mínima de 20 pregões contra {max20} em máxima",
        f"{sobrevend:.0f}% das ações com RSI abaixo de {REV_RSI_SOBREVENDA}",
        f"{n_conf} ações confirmando reversão, {n_estab} estabilizando e {n_queda} ainda fazendo mínimas",
    ]
    if ibov:
        leitura.insert(0, f"Ibovespa: {ibov['estagio'].lower()}"
                          + (f", {ibov['queda_pct']:.1f}% abaixo da máxima de 60 pregões" if ibov["queda_pct"] else ""))
    return {
        "n": n, "fase": fase, "acima_ema50": acima50, "acima_ema200": acima200, "delta_5d": delta5,
        "delta_20d": round(acima50 - acima50_20d, 1), "macd_positivo": macd_pos, "sobrevendidas": sobrevend,
        "novas_minimas": int(min20), "novas_maximas": int(max20),
        "estagios": est, "ibov": ({k: ibov[k] for k in ("estagio", "score", "sinais", "queda_pct", "idade_fundo")}
                                  if ibov else None),
        "ibov_dd12m": ibov_dd, "leitura": leitura,
    }


# ==============================================================================
# ANÁLISE DO ATIVO
# ==============================================================================

def analisar_ativo(acao, liquidez, fund=None, regime=None):
    df = baixar_historico(acao)
    if df is None:
        return None

    df = calcular_indicadores(df)

    colunas_obrigatorias = [
        "Close", "EMA9", "EMA21", "EMA50", "EMA200", "RSI",
        "MACD", "MACD_SIGNAL", "MACD_HIST", "ADX", "DI_PLUS",
        "DI_MINUS", "VOL_REL", "RESISTENCIA20"
    ]
    df = df.dropna(subset=colunas_obrigatorias)
    if len(df) < 50:
        return None

    HIST_CACHE[acao] = df

    atual = df.iloc[-1]
    dias_cross = identificar_cross_recente(df)
    pullback = identificar_pullback(df)
    reversao = identificar_reversao(df)

    # ---------------- score técnico (igual à V2.0) ----------------
    score = 0
    motivos = []

    s, m = score_tendencia(atual)
    score += s
    motivos.extend(m)

    s, m = score_setup(atual, dias_cross, pullback, reversao)
    score += s
    motivos.extend(m)

    s, m = score_rsi(atual)
    score += s
    motivos.extend(m)

    s, m = score_macd(atual)
    score += s
    motivos.extend(m)

    s, m = score_adx(atual)
    score += s
    motivos.extend(m)

    s, m = score_volume(atual)
    score += s
    motivos.extend(m)

    s, m = score_resistencia(atual)
    score += s
    motivos.extend(m)

    s, m = score_distancia_ema50(atual)
    score += s
    motivos.extend(m)

    score_tec = max(0, min(100, score))

    if dias_cross is not None:
        tipo_setup = "CRUZAMENTO EMA50"
    elif pullback:
        tipo_setup = "PULLBACK EMA50"
    elif reversao:
        tipo_setup = "REVERSÃO"
    else:
        tipo_setup = "SEM SETUP"

    if (atual["Close"] > atual["EMA50"] and atual["EMA50"] > atual["EMA200"] and atual["EMA200_UP"]):
        tendencia = "ALTA"
    elif (atual["Close"] < atual["EMA50"] and atual["EMA50"] < atual["EMA200"]):
        tendencia = "BAIXA"
    else:
        tendencia = "TRANSIÇÃO"

    if atual["MACD"] > atual["MACD_SIGNAL"] and atual["MACD_HIST"] > 0:
        macd_status = "POSITIVO"
    elif atual["MACD"] > atual["MACD_SIGNAL"]:
        macd_status = "VIRANDO"
    else:
        macd_status = "NEGATIVO"

    rsi = atual["RSI"]
    if rsi < 30:
        rsi_status = "SOBREVENDA"
    elif rsi < 45:
        rsi_status = "FRACO"
    elif rsi <= 60:
        rsi_status = "SAUDÁVEL"
    elif rsi <= 70:
        rsi_status = "FORTE"
    else:
        rsi_status = "SOBRECOMPRA"

    if dias_cross is None:
        momento = "-"
    elif dias_cross == 0:
        momento = "HOJE"
    elif dias_cross == 1:
        momento = "ONTEM"
    else:
        momento = f"{dias_cross} DIAS"

    # ---------------- NOVO: valor + cenário + score final ----------------
    fund = fund or {}
    score_val, motivos_val = score_valor(fund)

    beta, fr = np.nan, np.nan
    motivos_cen = []
    nome_regime = "-"

    if regime is None:
        # sem cenário disponível: usa só a técnica (comportamento da V2.0)
        final = score_tec
        ajuste = 0
    else:
        nome_regime = regime["REGIME"]
        w_tec, w_val, mult = PESOS_REGIME[nome_regime]

        beta, fr = metricas_vs_indice(df, regime["IBOV"])
        ajuste, motivos_cen = score_cenario(beta, fr, nome_regime)

        if _valido(score_val):
            base = w_tec * score_tec + w_val * score_val
        else:
            base = score_tec
            motivos_val.append("Score final usa só a técnica")

        final = base * mult + ajuste

        teto = None
        if tendencia == "BAIXA":
            teto = TETO_TENDENCIA_BAIXA
        elif tendencia == "TRANSIÇÃO" and nome_regime in ("CAUTELA", "RISK-OFF"):
            teto = TETO_TRANSICAO_DEFENSIVO
        if teto is not None and final > teto:
            final = teto
            motivos_cen.append(f"Score limitado a {teto} (tendência {tendencia} em {nome_regime})")

    final = max(0, min(100, final))
    sinal = classificar_sinal(final)

    # ---------------- NOVO (V3.1): pullback, Fibonacci e plano ----------------
    fib = calcular_fibonacci(df)
    av = avaliar_pullback(df, fib, fr)
    plano = montar_plano(df, fib)
    hist_pb = historico_pullback(df)
    oportunidade, op_notas = avaliar_oportunidade(
        av["estagio"], av["pb_score"], plano, tendencia,
        float(atual["Close"]), float(atual["EMA200"]), score_val, nome_regime)
    op_score = None
    if oportunidade:
        op_score = float(round(0.5 * av["pb_score"]
                               + 0.3 * min(plano["rr"] / 4, 1) * 100
                               + 0.2 * final, 1))

    # ---------------- NOVO (V3.2): reversão, checklist, líderes e projeção ----------------
    ibov_c = regime["IBOV"] if regime else None
    rev = avaliar_reversao(df)
    plano_rev = montar_plano_reversao(df, rev)
    rev_ok, rev_notas = avaliar_reversao_op(rev, plano_rev, nome_regime)
    lider = avaliar_lider(df, ibov_c, tendencia, fr)
    check, macd_gatilho = montar_checklist(df, av, fib)
    plano_proj = plano_rev if (rev_ok and not oportunidade) else plano
    proj = projecao_cenarios(df, plano_proj)
    proj["plano"] = "reversão" if plano_proj is plano_rev and plano_rev else "pullback"

    return {
        "AÇÃO": acao,
        "PREÇO": atual["Close"],
        "LIQUIDEZ": liquidez,
        "EMA9": atual["EMA9"],
        "EMA21": atual["EMA21"],
        "EMA50": atual["EMA50"],
        "EMA200": atual["EMA200"],
        "DIST_EMA50_%": atual["DIST_EMA50"],
        "RSI": atual["RSI"],
        "RSI_STATUS": rsi_status,
        "MACD": atual["MACD"],
        "MACD_SIGNAL": atual["MACD_SIGNAL"],
        "MACD_HIST": atual["MACD_HIST"],
        "MACD_STATUS": macd_status,
        "ADX": atual["ADX"],
        "DI+": atual["DI_PLUS"],
        "DI-": atual["DI_MINUS"],
        "VOL_REL": atual["VOL_REL"],
        "RESISTÊNCIA": atual["RESISTENCIA20"],
        "DIST_RES_%": atual["DIST_RESISTENCIA"],
        "TENDÊNCIA": tendencia,
        "SETUP": tipo_setup,
        "DIAS_CROSS": dias_cross,
        "MOMENTO": momento,
        # --- scores ---
        "SCORE": final,
        "SCORE_TEC": score_tec,
        "SCORE_VAL": score_val,
        "AJUSTE_CENARIO": ajuste,
        "REGIME": nome_regime,
        "SINAL": sinal,
        # --- fundamentos e risco ---
        "PL": fund.get("PL", np.nan),
        "PVP": fund.get("PVP", np.nan),
        "EV_EBITDA": fund.get("EV_EBITDA", np.nan),
        "ROE": fund.get("ROE", np.nan),
        "MRG_LIQ": fund.get("MRG_LIQ", np.nan),
        "DIV_PATRIM": fund.get("DIV_PATRIM", np.nan),
        "DY": fund.get("DY", np.nan),
        "CRESC_REC": fund.get("CRESC_REC", np.nan),
        "BETA": beta,
        "FORCA_REL": fr,
        # --- explicações ---
        "MOTIVOS": " | ".join(motivos),
        "MOTIVOS_VAL": " | ".join(motivos_val),
        "MOTIVOS_CEN": " | ".join(motivos_cen),
        # --- pullback, Fibonacci e plano da operação (V3.1) ---
        "ESTAGIO": av["estagio"],
        "PB_SCORE": av["pb_score"],
        "RETR_FIB": fib["retr"] if fib else np.nan,
        "NIVEL_FIB": av["nivel"],
        "CANDLE": av["candle"],
        "ATR_PCT": atual["ATR_PCT"],
        "FIB": fib,
        "PLANO": plano,
        "HIST_PB": hist_pb,
        "OPORTUNIDADE": oportunidade,
        "OP_SCORE": op_score,
        "OP_NOTAS": " | ".join(op_notas),
        "PB_MOTIVOS": " | ".join(av["motivos"]),
        # --- reversão, líderes, checklist e projeção (V3.2) ---
        "REV": rev,
        "PLANO_REV": plano_rev,
        "REV_OK": bool(rev_ok),
        "REV_NOTAS": " | ".join(rev_notas),
        "LIDER": lider,
        "CHECK": check,
        "MACD_GATILHO": bool(macd_gatilho),
        "PROJ": proj,
    }
