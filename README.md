# Ranking B3 no celular

Um site que mostra o ranking do Scanner B3 V3.2 e se atualiza sozinho todo dia útil, às 18h (horário de Brasília), depois do fechamento da bolsa. Você abre o link no celular e vê o resultado.

Como funciona: o GitHub roda o `gerar_dados.py` todo dia, que grava `docs/data.json`. A página `docs/index.html` lê esse arquivo e mostra o cenário de mercado, a lista de ações com filtros e, em cada ação, os detalhes e o gráfico.

## O que tem na pasta

| Arquivo | Para que serve |
|---|---|
| `b3_core.py` | Motor de cálculo (técnica, valor, cenário). Sem interface. |
| `scanner_b3_v3.py` | App de desktop (Tkinter). Usa o `b3_core.py`. |
| `gerar_dados.py` | Gera `docs/data.json` para o site. |
| `docs/index.html` | A página que você abre no celular. |
| `.github/workflows/atualizar.yml` | A rotina diária do GitHub. |
| `requirements.txt` | Bibliotecas do gerador. |

## Pullback, Fibonacci e plano da operação (novo na V3.1)

Para cada ação, o sistema procura a última perna de alta (até 60 pregões), calcula o recuo de Fibonacci (23,6% a 78,6%) e classifica o estágio: sem pullback, em correção, no suporte (zona 38,2% a 61,8%), retomando ou rompeu. A qualidade do pullback vai de 0 a 100 (tendência, força relativa, profundidade, volume secando, RSI e candle de confirmação).

O plano sai por fórmula: entrada no preço atual, stop abaixo da menor mínima de 10 pregões e do nível de Fibonacci logo abaixo (menos 0,5 ATR), alvo 1 na máxima anterior e alvo 2 na extensão de 27,2%. O gráfico projeta tudo à direita da última vela.

Uma ação vira **Oportunidade** quando está no suporte ou retomando, com qualidade 60 ou mais, ganho/risco de 2:1 ou mais (3:1 em RISK-OFF), tendência não baixista, preço acima da EMA200 e valor mínimo aceitável. O filtro é rigoroso de propósito: pode haver dias sem nenhuma. Cada ação mostra também quantas vezes o pullback na EMA50 deu certo no passado dela (amostra pequena é sinalizada). O selo "Novo hoje" aparece só a partir do segundo dia de execução. Nada disso é recomendação de compra.

Os pontos de ajuste ficam no topo do `b3_core.py` (`RR_MIN`, `PB_MIN`, `STOP_MAX_PCT`, `FIB_ZONA`...). O histórico baixado agora é de 5 anos (`PERIODO_HISTORICO`).

## Reversão, líderes e termômetro (novo na V3.2)

Quando o mercado cai, procurar pullback em tendência de alta deixa de ser o único jogo. A V3.2 separa o que o preço mostra do que ninguém prevê (notícias, política) e organiza o ranking em listas:

- **Oportunidades:** pullback no suporte de Fibonacci, como na V3.1.
- **Reversão:** ações que caíram forte e estão virando, em três etapas. *Capitulação* (RSI abaixo de 30, volume e volatilidade em pico, preço esticado abaixo da EMA50), *estabilização* (o fundo segura, sem nova mínima, com fundo mais alto ou divergência de alta no RSI) e *confirmação* (fecha acima da EMA9 e EMA21, histograma do MACD subindo, MACD acima da sinal, volume nos dias de alta). Estágios: em queda, capitulação, estabilizando, confirmando, confirmada e recuperada (tarde demais). A reversão só vira "confirmada" com plano válido (stop de até 10%) e ganho/risco mínimo de 2:1 (3:1 em mercado defensivo).
- **Líderes:** ações que seguram preço acima da EMA50 e EMA200 e superam o Ibovespa em 20 e 60 pregões, a no máximo 10% da máxima. Costumam ser as primeiras a subir na virada.
- **Termômetro do mercado:** percentual de ações acima da EMA50 e EMA200 (e a variação em 5 pregões), mínimas contra máximas de 20 pregões, ações sobrevendidas, distribuição por estágio e a leitura do Ibovespa nas três etapas. Resume em uma fase: queda aberta, fundo em formação, reversão em curso ou tendência saudável.

A página abre sozinha na lista mais útil do dia: Oportunidades, depois Reversão ou Líderes se o mercado estiver defensivo.

**Checklist do setup.** Cada ação mostra 12 condições (tendência, pullback, MACD e gatilho). O MACD entra como confirmação e não como exigência: ele atrasa e costuma estar negativo justamente no suporte.

**Projeção por cenários.** Faixas de preço em 10, 20 e 30 pregões (percentis 10, 50 e 90 dos retornos reais do papel nos últimos 3 anos) e a frequência com que o papel chegou ao alvo ou ao stop com a mesma distância do plano, entrando em qualquer dia. É base histórica, não previsão.

Ajustes ficam no topo do `b3_core.py` (`REV_QUEDA_MIN`, `REV_RR_MIN`, `LIDER_FR_MIN`, `PROJ_HORIZONTES`...).

## Passo a passo (cerca de 10 minutos)

**Antes de tudo, teste no seu computador**, para ter certeza de que as fontes de dados respondem:

```
pip install -r requirements.txt
python gerar_dados.py
python -m http.server 8000 --directory docs
```

Abra `http://localhost:8000` no navegador. Se o ranking aparecer, está tudo certo para publicar.

**Publicar:**

1. Crie uma conta gratuita em github.com (se ainda não tiver).
2. Clique em **New repository**, dê o nome `b3-ranking`, marque **Public** e crie.
3. Descompacte o zip e envie todo o conteúdo para o repositório (**Add file → Upload files** e arraste tudo). A pasta `.github` precisa ir junto. Se o navegador não enviar pastas que começam com ponto, use **Add file → Create new file**, digite `.github/workflows/atualizar.yml` como nome e cole o conteúdo do arquivo.
4. Vá em **Settings → Pages**. Em **Source** escolha **Deploy from a branch**, depois a branch **main** e a pasta **/docs**, e salve.
5. Vá na aba **Actions**, escolha **Atualizar ranking** e clique em **Run workflow**. Leva uns 3 a 5 minutos. Se der erro de permissão ao salvar, vá em **Settings → Actions → General → Workflow permissions**, marque **Read and write permissions** e rode de novo.
6. Seu link será `https://SEU-USUARIO.github.io/b3-ranking/`. No celular, abra o link e use **Adicionar à tela inicial** para ele virar um ícone.

## Pontos de atenção

- **O repositório é público.** Qualquer pessoa que souber o endereço consegue ver o ranking. Repositório privado com Pages exige plano pago do GitHub.
- **Fontes gratuitas podem falhar.** Yahoo Finance e Fundamentus às vezes bloqueiam ou limitam acessos vindos de servidores. Se isso acontecer, a execução fica marcada como falha (o GitHub avisa por e-mail) e o ranking anterior continua no ar. Basta rodar de novo em **Run workflow**.
- **Horários não são exatos.** O GitHub pode atrasar execuções agendadas em alguns minutos. Se o repositório ficar muito tempo sem atividade, ele pode pausar a agenda; é só rodar manualmente uma vez para reativar.
- **Feriados:** a página mostra a data do pregão. Em dia sem negociação, ela repete a data anterior.
- **Histórico:** a cada dia o gerador guarda um resumo em `docs/historico/`. Essa pasta serve de base para um backtest futuro.

Este material é uma triagem automática por regras e não é recomendação de investimento.
