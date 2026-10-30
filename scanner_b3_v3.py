# ==============================================================================
# SCANNER QUANTITATIVO B3 V3.0  -  APP DE DESKTOP (Tkinter)
# O motor de cálculo está em b3_core.py (mesma pasta).
# ==============================================================================

import datetime
import threading
import time

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog

import pandas as pd
import mplfinance as mpf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from b3_core import (
    QTDE_ATIVOS, PAUSA, PERIODO_FR, PESOS_REGIME, HIST_CACHE, HIST_HORIZONTE,
    calcular_regime, obter_acoes_mais_liquidas, analisar_ativo,
    baixar_historico, calcular_indicadores, _valido,
)


# ==============================================================================
# INTERFACE TKINTER
# ==============================================================================

def _fmt(v, formato="{:.1f}"):
    return "-" if not _valido(v) else formato.format(v)


def _plano_rr(plano):
    return f"{plano['rr']:.1f}" if isinstance(plano, dict) else "-"


def criar_figura(df, acao, plano=None, fib=None, dias_projecao=15):
    """Candles + EMAs + volume. Com plano/Fibonacci, projeta os níveis à direita do último candle."""
    cols = ["Open", "High", "Low", "Close", "Volume", "EMA9", "EMA21", "EMA50", "EMA200"]
    df_plot = df.tail(180)[cols].copy()
    projetar = bool(plano or fib)
    if projetar:
        futuro = pd.bdate_range(start=df_plot.index[-1] + pd.Timedelta(days=1), periods=dias_projecao)
        df_plot = pd.concat([df_plot, pd.DataFrame(float("nan"), index=futuro, columns=cols)])

    mc = mpf.make_marketcolors(up='green', down='red', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=False)

    apds = [
        mpf.make_addplot(df_plot["EMA9"], color='#1f77b4', width=1.1),
        mpf.make_addplot(df_plot["EMA21"], color='#ff7f0e', width=1.3),
        mpf.make_addplot(df_plot["EMA50"], color='#2ca02c', width=1.6),
        mpf.make_addplot(df_plot["EMA200"], color='#d62728', width=1.9),
    ]

    rotulos = []
    if fib:
        rotulos += [("Fib 38,2%", fib["niveis"]["38.2"], "#9467bd"),
                    ("Fib 50%", fib["niveis"]["50.0"], "#9467bd"),
                    ("Fib 61,8%", fib["niveis"]["61.8"], "#9467bd")]
    if plano:
        rotulos += [("Entrada", plano["entrada"], "#1f77b4"),
                    ("Stop", plano["stop"], "#d62728"),
                    ("Alvo 1", plano["alvo"], "#2ca02c"),
                    ("Alvo 2", plano["alvo2"], "#0f7a3a")]
    kwargs = {}
    if rotulos:
        kwargs["hlines"] = dict(hlines=[r[1] for r in rotulos], colors=[r[2] for r in rotulos],
                                linestyle="--", linewidths=0.9)

    fig, axes = mpf.plot(
        df_plot,
        type='candle',
        style=s,
        volume=True,
        addplot=apds,
        returnfig=True,
        figsize=(10, 6),
        title=f"{acao}  |  Candlestick + EMAs + Volume",
        ylabel="Preço",
        ylabel_lower="Volume",
        tight_layout=True,
        **kwargs
    )

    ax = axes[0]
    from matplotlib.lines import Line2D
    ax.legend(handles=[Line2D([0], [0], color='#1f77b4', lw=1.2, label="EMA9"),
                       Line2D([0], [0], color='#ff7f0e', lw=1.3, label="EMA21"),
                       Line2D([0], [0], color='#2ca02c', lw=1.6, label="EMA50"),
                       Line2D([0], [0], color='#d62728', lw=1.9, label="EMA200")], loc="upper left")
    if projetar:
        x = len(df_plot) - 1
        if plano:
            y0, y1 = ax.get_ylim()
            ax.set_ylim(min(y0, plano["stop"] * 0.99), max(y1, plano["alvo2"] * 1.01))
        y0, y1 = ax.get_ylim()
        passo = (y1 - y0) * 0.032            # distância mínima entre rótulos
        itens = sorted(rotulos, key=lambda r: r[1])
        posicoes = []
        for _, preco, _ in itens:
            pos = preco if not posicoes else max(preco, posicoes[-1] + passo)
            posicoes.append(pos)
        for (nome, preco, cor), pos in zip(itens, posicoes):
            ax.text(x, pos, f"{nome} {preco:.2f}", color=cor, fontsize=8, ha="right", va="center",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
        ax.set_ylim(y0, max(y1, posicoes[-1] + passo))
    return fig


class ScannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Scanner Quantitativo B3 V3.0")
        self.root.geometry("1760x1000")
        self.root.minsize(1450, 850)

        self.ranking = pd.DataFrame()
        self.regime = None
        self.scanning = False

        self.criar_interface()

    def criar_interface(self):
        # ===== TOPO =====
        frame_top = ttk.Frame(self.root, padding=8)
        frame_top.pack(fill=tk.X)

        self.btn_scan = ttk.Button(frame_top, text=f"▶  Iniciar Scanner ({QTDE_ATIVOS} mais líquidas)",
                                   command=self.iniciar_scanner)
        self.btn_scan.pack(side=tk.LEFT, padx=5)

        self.btn_csv = ttk.Button(frame_top, text="💾  Exportar CSV", command=self.exportar_csv,
                                  state="disabled")
        self.btn_csv.pack(side=tk.LEFT, padx=5)

        self.lbl_status = ttk.Label(frame_top, text="Pronto", font=("Segoe UI", 10))
        self.lbl_status.pack(side=tk.LEFT, padx=15)

        self.progress = ttk.Progressbar(frame_top, mode="determinate", length=350)
        self.progress.pack(side=tk.LEFT, padx=10)

        # ===== CENÁRIO DE MERCADO =====
        frame_reg = ttk.LabelFrame(self.root, text="Cenário de mercado", padding=6)
        frame_reg.pack(fill=tk.X, padx=8, pady=2)

        self.lbl_regime = ttk.Label(frame_reg, text="Ainda não calculado. Clique em Iniciar Scanner.",
                                    font=("Segoe UI", 11, "bold"))
        self.lbl_regime.pack(anchor="w")
        self.lbl_regime_det = ttk.Label(frame_reg, text="", font=("Consolas", 9), justify=tk.LEFT)
        self.lbl_regime_det.pack(anchor="w")

        # ===== LOG =====
        frame_log = ttk.LabelFrame(self.root, text="Log de execução", padding=4)
        frame_log.pack(fill=tk.X, padx=8, pady=4)

        self.txt_log = scrolledtext.ScrolledText(frame_log, height=6, font=("Consolas", 9))
        self.txt_log.pack(fill=tk.X)

        # ===== PRINCIPAL =====
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)

        # ----- ESQUERDA (tabela) -----
        frame_left = ttk.Frame(paned)
        paned.add(frame_left, weight=3)

        cols = ("AÇÃO", "SCORE", "TEC", "VAL", "SINAL", "OPORT.", "ESTÁGIO", "PB", "G/R", "SETUP",
                "PREÇO", "RSI", "ADX", "VOL", "P/L", "P/VP", "DY%", "BETA", "TENDÊNCIA")
        self.tree = ttk.Treeview(frame_left, columns=cols, show="headings", height=24)

        larguras = {
            "AÇÃO": 70, "SCORE": 52, "TEC": 45, "VAL": 45, "SINAL": 150, "SETUP": 130,
            "PREÇO": 68, "RSI": 48, "ADX": 48, "VOL": 55, "P/L": 50, "P/VP": 50,
            "DY%": 50, "BETA": 48, "TENDÊNCIA": 88,
            "OPORT.": 58, "ESTÁGIO": 105, "PB": 40, "G/R": 45,
        }
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=larguras.get(col, 70), anchor="center")

        self.tree.tag_configure("exc", background="#b7f0c0")
        self.tree.tag_configure("forte", background="#d6f5d6")
        self.tree.tag_configure("int", background="#eef9e0")
        self.tree.tag_configure("agu", background="#fff6d6")
        self.tree.tag_configure("evi", background="#f6e3e3")

        vsb = ttk.Scrollbar(frame_left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # ----- DIREITA (gráfico) -----
        frame_right = ttk.Frame(paned)
        paned.add(frame_right, weight=2)

        self.frame_info = ttk.LabelFrame(frame_right, text="Detalhes da Ação Selecionada", padding=6)
        self.frame_info.pack(fill=tk.X, padx=5, pady=3)

        self.lbl_info = ttk.Label(self.frame_info, text="Selecione uma ação na tabela à esquerda",
                                  font=("Consolas", 9), justify=tk.LEFT, wraplength=760)
        self.lbl_info.pack(anchor="w")

        self.frame_chart = ttk.Frame(frame_right)
        self.frame_chart.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, msg):
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.root.update_idletasks()

    def iniciar_scanner(self):
        if self.scanning:
            return
        self.scanning = True
        self.btn_scan.config(state="disabled")
        self.btn_csv.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.txt_log.delete(1.0, tk.END)
        self.lbl_status.config(text="Iniciando...")
        self.progress["value"] = 0
        HIST_CACHE.clear()

        threading.Thread(target=self.executar_scanner, daemon=True).start()

    def executar_scanner(self):
        try:
            self.root.after(0, lambda: self.log("=" * 100))
            self.root.after(0, lambda: self.log("SCANNER QUANTITATIVO B3 V3.0"))
            self.root.after(0, lambda: self.log("=" * 100))

            # ---- 1) cenário de mercado ----
            self.root.after(0, lambda: self.atualizar_status("Calculando cenário de mercado..."))
            self.root.after(0, lambda: self.log("Calculando cenário de mercado (Ibovespa, dólar, Selic)...\n"))
            regime = calcular_regime()
            self.regime = regime
            if regime is None:
                self.root.after(0, lambda: self.log(
                    "AVISO: não foi possível calcular o cenário. Ranking usará só a técnica.\n"))
                self.root.after(0, lambda: self.lbl_regime.config(text="Cenário indisponível (ranking só técnico)"))
            else:
                self.root.after(0, lambda r=regime: self.mostrar_regime(r))

            # ---- 2) universo + fundamentos ----
            self.root.after(0, lambda: self.log(f"Obtendo as {QTDE_ATIVOS} ações mais líquidas do Fundamentus...\n"))
            df_universo = obter_acoes_mais_liquidas()

            if df_universo.empty:
                self.root.after(0, lambda: messagebox.showerror("Erro", "Não foi possível obter as ações."))
                return

            total = len(df_universo)
            self.root.after(0, lambda: self.log(f"Universo carregado: {total} ações\n"))
            self.root.after(0, lambda: self.log("Iniciando análise dos ativos...\n"))

            resultados = []
            falhas = []

            for i, linha in df_universo.iterrows():
                acao = str(linha["AÇÃO"]).strip().upper()
                liquidez = linha["LIQUIDEZ"]

                self.root.after(0, lambda a=acao, idx=i: self.atualizar_status(f"Analisando {a} ({idx+1}/{total})"))
                self.root.after(0, lambda v=(i+1)/total*100: self.progress.configure(value=v))

                resultado = analisar_ativo(acao, liquidez, fund=linha.to_dict(), regime=regime)

                if resultado is None:
                    falhas.append(acao)
                    self.root.after(0, lambda a=acao, idx=i: self.log(f"[{idx+1:02d}/{total}] {a:<8} -> sem histórico"))
                else:
                    resultados.append(resultado)
                    self.root.after(0, lambda a=acao, r=resultado, idx=i: self.log(
                        f"[{idx+1:02d}/{total}] {a:<8} -> {r['SETUP']:<18} "
                        f"Final={r['SCORE']:>5.1f} (Téc {r['SCORE_TEC']:>3.0f} | Val {_fmt(r['SCORE_VAL'], '{:.0f}'):>3})  {r['SINAL']}"))

                time.sleep(PAUSA)

            self.root.after(0, lambda: self.log("\n" + "=" * 100))
            self.root.after(0, lambda: self.log(
                f"Análise concluída → Sucessos: {len(resultados)}  |  Falhas: {len(falhas)}"))
            self.root.after(0, lambda: self.log("=" * 100))

            if not resultados:
                self.root.after(0, lambda: messagebox.showinfo("Aviso", "Nenhum resultado encontrado.\nVeja o log."))
                return

            self.ranking = pd.DataFrame(resultados)
            self.ranking = self.ranking.sort_values(
                by=["SCORE", "DIST_EMA50_%"],
                ascending=[False, True]
            ).reset_index(drop=True)

            self.root.after(0, self.preencher_tabela)
            self.root.after(0, lambda: self.btn_csv.config(state="normal"))
            self.root.after(0, lambda: self.lbl_status.config(text=f"Concluído! {len(self.ranking)} ações analisadas"))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"\nERRO GERAL: {e}"))
            self.root.after(0, lambda: messagebox.showerror("Erro", str(e)))
        finally:
            self.root.after(0, self.finalizar_scanner)

    def mostrar_regime(self, r):
        w_tec, w_val, mult = PESOS_REGIME[r["REGIME"]]
        self.lbl_regime.config(
            text=f"Regime: {r['REGIME']}   (pontuação {r['PONTOS']:+d})   |   "
                 f"pesos: técnica {w_tec:.0%} / valor {w_val:.0%}   |   multiplicador {mult:.2f}")
        self.lbl_regime_det.config(text="\n".join(r["DETALHES"]))
        self.log(f"Regime de mercado: {r['REGIME']} ({r['PONTOS']:+d})")
        for d in r["DETALHES"]:
            self.log("   " + d)
        self.log("")

    def atualizar_status(self, texto):
        self.lbl_status.config(text=texto)

    def finalizar_scanner(self):
        self.scanning = False
        self.btn_scan.config(state="normal")
        self.progress["value"] = 100

    def preencher_tabela(self):
        self.tree.delete(*self.tree.get_children())
        for _, row in self.ranking.iterrows():
            sinal = row["SINAL"]
            tag = {"ENTRADA EXCELENTE": "exc", "ENTRADA FORTE": "forte",
                   "ENTRADA INTERESSANTE": "int", "AGUARDAR CONFIRMAÇÃO": "agu"}.get(sinal, "evi")
            valores = (
                row["AÇÃO"],
                f"{row['SCORE']:.0f}",
                f"{row['SCORE_TEC']:.0f}",
                _fmt(row["SCORE_VAL"], "{:.0f}"),
                sinal,
                "SIM" if row["OPORTUNIDADE"] else "-",
                row["ESTAGIO"],
                _fmt(row["PB_SCORE"], "{:.0f}"),
                _plano_rr(row["PLANO"]),
                row["SETUP"],
                f"{row['PREÇO']:.2f}",
                f"{row['RSI']:.1f}",
                f"{row['ADX']:.1f}",
                f"{row['VOL_REL']:.2f}x",
                _fmt(row["PL"]),
                _fmt(row["PVP"], "{:.2f}"),
                _fmt(row["DY"]),
                _fmt(row["BETA"], "{:.2f}"),
                row["TENDÊNCIA"],
            )
            self.tree.insert("", tk.END, values=valores, tags=(tag,))

    def exportar_csv(self):
        if self.ranking.empty:
            return
        caminho = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"ranking_b3_{datetime.date.today():%Y%m%d}.csv",
            filetypes=[("CSV", "*.csv")])
        if not caminho:
            return
        try:
            # ';' e vírgula decimal para abrir direto no Excel em português
            saida = self.ranking.drop(columns=["FIB", "PLANO", "HIST_PB"], errors="ignore").copy()
            for k in ("entrada", "stop", "alvo", "alvo2", "rr"):
                saida["PLANO_" + k.upper()] = self.ranking["PLANO"].map(
                    lambda p, k=k: p[k] if isinstance(p, dict) else None)
            saida.to_csv(caminho, sep=";", decimal=",", index=False, encoding="utf-8-sig")
            messagebox.showinfo("Exportado", f"Arquivo salvo em:\n{caminho}")
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def on_select(self, event):
        selecionado = self.tree.selection()
        if not selecionado:
            return
        item = self.tree.item(selecionado[0])
        acao = str(item["values"][0])
        self.mostrar_detalhes(acao)
        self.plotar_grafico(acao)

    def mostrar_detalhes(self, acao):
        row = self.ranking[self.ranking["AÇÃO"] == acao].iloc[0]
        plano = row["PLANO"] if isinstance(row["PLANO"], dict) else None
        fib = row["FIB"] if isinstance(row["FIB"], dict) else None
        hist = row["HIST_PB"] if isinstance(row["HIST_PB"], dict) else None
        linhas_pb = (f"Pullback: {row['ESTAGIO']}   Qualidade: {_fmt(row['PB_SCORE'], '{:.0f}')}/100   "
                     f"Oportunidade: {'SIM' if row['OPORTUNIDADE'] else 'não'}\n")
        if fib:
            linhas_pb += (f"Fibonacci: recuo de {row['RETR_FIB'] * 100:.0f}% (mais perto de {row['NIVEL_FIB']})   "
                          f"perna {fib['baixo']:.2f} -> {fib['alto']:.2f}\n")
        if plano:
            linhas_pb += (f"Plano: entrada {plano['entrada']:.2f}   stop {plano['stop']:.2f} (-{plano['risco_pct']:.1f}%)   "
                          f"alvo 1 {plano['alvo']:.2f} (+{plano['ganho_pct']:.1f}%)   alvo 2 {plano['alvo2']:.2f}   "
                          f"ganho/risco {plano['rr']:.1f}\n")
        if hist:
            linhas_pb += (f"Histórico do setup: {hist['n']} ocorrências, acerto {hist['taxa']:.0f}% "
                          f"(alvo de 2x o risco em {HIST_HORIZONTE} pregões), esperança {hist['esperanca_r']:+.2f}R\n")
        texto = (
            f"{acao}  |  Score final: {row['SCORE']:.0f}  (Técnica {row['SCORE_TEC']:.0f} | "
            f"Valor {_fmt(row['SCORE_VAL'], '{:.0f}')} | Ajuste cenário {row['AJUSTE_CENARIO']:+d})  |  {row['SINAL']}\n"
            f"Regime: {row['REGIME']}   Beta: {_fmt(row['BETA'], '{:.2f}')}   "
            f"Força rel. {PERIODO_FR}d vs Ibov: {_fmt(row['FORCA_REL'], '{:+.1f}')} p.p.\n"
            f"Preço: R$ {row['PREÇO']:.2f}   EMA9: {row['EMA9']:.2f}   EMA21: {row['EMA21']:.2f}   "
            f"EMA50: {row['EMA50']:.2f}   EMA200: {row['EMA200']:.2f}\n"
            f"Dist. EMA50: {row['DIST_EMA50_%']:.2f}%   RSI: {row['RSI']:.1f} ({row['RSI_STATUS']})   "
            f"MACD: {row['MACD_STATUS']}   ADX: {row['ADX']:.1f}   Vol: {row['VOL_REL']:.2f}x\n"
            f"Setup: {row['SETUP']}   Tendência: {row['TENDÊNCIA']}   Momento: {row['MOMENTO']}\n"
            f"Fundamentos: P/L {_fmt(row['PL'])}  P/VP {_fmt(row['PVP'], '{:.2f}')}  "
            f"EV/EBITDA {_fmt(row['EV_EBITDA'])}  ROE {_fmt(row['ROE'])}%  "
            f"Mrg.Líq {_fmt(row['MRG_LIQ'])}%  DY {_fmt(row['DY'])}%  "
            f"Dív.Líq/PL {_fmt(row['DIV_PATRIM'], '{:.2f}')}\n"
            f"{linhas_pb}"
            f"Motivos técnicos: {row['MOTIVOS']}\n"
            f"Motivos de valor: {row['MOTIVOS_VAL']}\n"
            f"Cenário: {row['MOTIVOS_CEN'] or '-'}"
        )
        self.lbl_info.config(text=texto)

    def plotar_grafico(self, acao):
        for widget in self.frame_chart.winfo_children():
            widget.destroy()

        df = HIST_CACHE.get(acao)
        if df is None:
            df = baixar_historico(acao)
            if df is None:
                return
            df = calcular_indicadores(df)
        plano = fib = None
        linha = self.ranking[self.ranking["AÇÃO"] == acao]
        if not linha.empty:
            p, f = linha.iloc[0]["PLANO"], linha.iloc[0]["FIB"]
            plano = p if isinstance(p, dict) else None
            fib = f if isinstance(f, dict) else None
        fig = criar_figura(df, acao, plano, fib)

        self.canvas = FigureCanvasTkAgg(fig, master=self.frame_chart)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar = NavigationToolbar2Tk(self.canvas, self.frame_chart)
        toolbar.update()


# ==============================================================================
# EXECUÇÃO
# ==============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = ScannerApp(root)
    root.mainloop()
