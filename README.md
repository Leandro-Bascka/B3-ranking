# Ranking B3 no celular

Um site que mostra o ranking do Scanner B3 V3.0 e se atualiza sozinho todo dia útil, às 18h (horário de Brasília), depois do fechamento da bolsa. Você abre o link no celular e vê o resultado.

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
