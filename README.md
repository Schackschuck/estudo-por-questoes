# Estudo por questões

Um sistema pessoal de estudo que descobre **em que assunto eu estou fraco** a partir das questões que eu resolvo.

Cada questão é etiquetada numa árvore de conceitos (hoje, 314 tags de botânica). A cada resposta, o app atualiza o domínio de cada conceito, com incerteza, herança pela árvore e atribuição cruzada quando uma questão envolve dois conceitos. O painel mostra o que estudar primeiro: o ramo que caiu e, dentro dele, a tag mais fraca.

A ideia que o projeto testa: **dá para diagnosticar o que eu não sei só contando acertos e erros na árvore, sem chamar IA na rotina.** A IA aparece só em dois botões opcionais (sugerir tags ao importar questões e escrever um relatório em texto). Sem chave de API, o app funciona inteiro.

- Python com a biblioteca padrão (servidor HTTP e SQLite) + PyYAML. Sem framework.
- Abre no navegador do PC e do celular, na mesma rede Wi-Fi, sem login.
- Banco com 252 questões, uma por conceito, auditadas por um carregador que recusa questões com "pista formal" (alternativa correta mais longa que as outras) e balanceia as letras.
- 12 critérios de aceitação automatizados em `testes.py`.

Versão 0.2: fases 1 a 4 da especificação. A fase 5 é usar de verdade por três semanas antes de construir qualquer coisa a mais. As referências a "seção N" no código e neste README apontam para a especificação original, que não está no repositório.

## Rodar

```
pip install -r requirements.txt
python carregar_questoes.py   # só na primeira vez: carrega o banco de questões
python app.py
```

Abre sozinho no navegador. O endereço do celular aparece no terminal — mesma
rede Wi‑Fi, sem deploy, sem login. Para parar: `Ctrl+C`.

Conferir os 12 critérios de aceitação da seção 14:

```
python testes.py
```

## Os arquivos

| arquivo | o que é |
|---|---|
| `config.py` | **todos** os números do sistema, com a justificativa ao lado |
| `taxonomia/biologia.yaml` | a árvore de conceitos — é aqui que você mexe no conteúdo |
| `taxonomia.py` | lê o YAML, valida e materializa `tag_ancestral` |
| `db.py` | schema SQLite (seção 12, incluindo as colunas da v0.3 já presentes e nulas) |
| `importador.py` | parser do texto colado + checagem de pista formal |
| `modelo.py` | modelo do aluno: domínio, herança, atribuição cruzada, quarentena. **Zero LLM** |
| `llm.py` | a camada única e trocável de LLM. Apague o arquivo e o app continua inteiro |
| `app.py` | servidor + API |
| `web/` | as quatro telas |
| `questoes/*.json` | banco de 252 questões de botânica, uma por conceito |
| `carregar_questoes.py` | carrega os JSON no banco e audita pista formal |
| `estudo.db` | seu banco. Nasce no primeiro `python app.py` |

## Mexer na árvore de conceitos

Edite `taxonomia/biologia.yaml` e reinicie o app (ou clique em qualquer coisa
que chame `/api/rebuild`). Regras que o carregador cobra:

- **id é chave, não rótulo.** Nunca renomeie um id, nunca reaproveite. O nome
  bonito vive em `nome:` e pode mudar à vontade.
- Para pendurar a mesma tag em dois ramos, **repita só o id**, sem `nome:`.
  Isso é referência, não duplicata. Hoje 47 tags têm mais de um pai.
- Três níveis abaixo da disciplina. O carregador avisa se você passar disso.
- `diagnostica: false` tira a tag do cálculo de domínio (vocabulário puro).

Mover um ramo não invalida nada: tentativa guarda `tag.id`, nunca caminho.
O hash do arquivo fica gravado junto do estado, para separar "aprendi" de
"editei o YAML".

Estado atual: **314 tags** de botânica — 262 folhas, 52 nós internos, e um ramo
transversal `bio.caracteres_diagnosticos` com 17 pares de confusão
(paralelinérvea × reticulada, colênquima × esclerênquima, ovário súpero ×
ínfero, fruto × pseudofruto…).

## As quatro decisões pendentes (seção 16)

Decidi as quatro para destravar a fase 1. Todas vivem em `config.py` e mudar
qualquer uma é editar uma linha.

1. **Eliminar alternativa é opcional no modo estudo.** O critério de sucesso
   mais difícil é "eu continuei usando na terceira semana"; atrito obrigatório
   na rotina diária é o jeito mais rápido de perder isso. No modo triagem, que
   é ocasional, a instrumentação completa continua sendo pedida.
   → `ELIMINACAO_OBRIGATORIA`

   Com uma exceção: **se você riscar todas as outras alternativas, a pergunta
   de confiança não aparece**. Riscar as outras já é a declaração — o raciocínio
   está registrado na sequência de eliminações, com timestamp. A tentativa é
   gravada com `confianca_declarada = 'eliminacao_completa'`, um valor próprio
   e não `'certeza'`, para o sistema nunca inventar uma declaração que você não
   fez. Para o sinal de quarentena ela vale como certeza; para a classificação
   de "descuido", não — quem eliminou tudo e ainda errou não foi descuidado.
2. **Cobertura suficiente = peso 5, não contagem de questões.** Uma questão
   secundária vale 0,5 e uma herança de ancestral vale menos ainda, então
   contar questões mediria a coisa errada. Abaixo de peso 1 a tag é cinza.
   → `PESO_MINIMO_SINAL`, `PESO_MEDIDA`
3. **"Não preciso desse conteúdo" exclui o ramo inteiro** abaixo da tag.
   Ninguém descarta um conceito solto, descarta um assunto. Reversível no
   painel (clique na tag riscada). → `EXCLUIR_RAMO_INTEIRO`
4. **Peso da secundária continua 0,5**, mas agora com saída: depois de 30
   tentativas em cada papel, o painel mostra o número que o seu próprio dado
   sugere (`modelo.calibrar_peso_secundaria`). → `PESO_SECUNDARIA`

E os limiares que a spec avisa que fariam o projeto empacar na fase 4:
fraca se o **teto** do intervalo fica abaixo de 0,60; dominada se o **piso**
passa de 0,80. Usar teto e piso em vez do ponto é o que garante que pouca
evidência nunca vire acusação de fraqueza.

## Como o diagnóstico funciona, em uma passada

Cada tentativa vira peso, não contagem:

```
peso = peso_do_papel × decaimento_por_exposição × decaimento_por_distância
       (1,0 ou 0,5)     (1ª=1  2ª=0,6  3ª=0,3)      (0,6 ^ distância)
```

O nível é `(acertos + 1) / (peso + 2)` — prior Beta(1,1), que é o que faz três
acertos não valerem trinta. A incerteza sai do mesmo `n`.

O que você **riscou** também entra. Eliminar um distrator é conhecimento
demonstrado — você sabia que aquilo estava errado — e esse dado já ficava
gravado em `evento` sem ser usado. Agora ele conta duas vezes:

- **peso da tentativa** × `1 + 0,5 × fração riscada corretamente`. Acertar
  tendo riscado os três distratores vale 1,5 observações, não 1;
- **crédito parcial no erro**: errar depois de reduzir o campo vale
  `0,3 × fração` em vez de zero. Você não fechou, mas eliminou.

E riscar a alternativa **certa** e errar não é ausência de conhecimento: é uma
concepção errada forte o bastante para você agir sobre ela. Essa tentativa
carrega dois fatos independentes — escolheu errado *e* rejeitou o certo —
então **vale por duas observações**, e o erro é classificado como `equivoco`,
distinto de `conceitual`.

Não existe "domínio negativo", e não é uma limitação: `nivel` é a probabilidade
de você acertar a próxima questão da tag, e probabilidade tem piso em zero.
Forçar negativo quebraria `incerteza`, que é `sqrt(nivel × (1-nivel))`. O que o
modelo registra no lugar é **errado com convicção**, que se escreve como mais
peso puxando para baixo — e é mais informativo que um valor negativo, porque
distingue "errou uma vez" de "erra de forma confiável".

Numa questão de 4 alternativas, a escala inteira:

| o que aconteceu | peso | domínio estimado | tipo |
|---|---|---|---|
| acertou riscando os 3 distratores | 1,50 | 71,4% | — |
| acertou sem riscar nada | 1,00 | 66,7% | — |
| errou riscando 2 distratores certos | 1,33 | 38,0% | conceitual |
| errou sem riscar nada | 1,00 | 33,3% | conceitual |
| **errou e riscou a alternativa certa** | **2,00** | **25,0%** | **equivoco** |

→ `BONUS_ELIMINACAO`, `CREDITO_ELIMINACAO`, `PENALIDADE_RISCAR_CERTA`

`equivoco` sobe 20% na ordem da trilha e a justificativa diz o que fazer:
concepção errada se trata voltando ao material, não fazendo mais questões —
o mesmo tratamento da lacuna, por motivo oposto (vazio × preenchido errado).

A fração é **derivada de `evento`** a cada cálculo, nunca guardada em coluna:
se você mudar as constantes, o histórico inteiro se recalcula sozinho.
Eliminar e depois desmarcar não conta — vale o saldo líquido.

Duas coisas acontecem antes disso:

- **Herança.** Uma questão de `bio.procambio` também é evidência para
  `bio.meristemas` e `bio.histologia_vegetal`. Um ancestral recebe o **maior**
  peso entre as tags da questão, nunca a soma — senão uma questão com três
  tags do mesmo ramo contaria três vezes para o pai.
- **Atribuição por cruzamento.** Numa questão multi-tag errada, a evidência
  negativa não se divide igual: concentra na tag cujo histórico já é mais
  fraco. É o que permite dizer "errei as de procâmbio que também traziam
  protoderme, logo o problema é protoderme" sem etiquetar distrator e sem LLM.

## Recompensa de gesto

Marcar uma alternativa dá **+1**; riscar uma dá **+3**, com a plaquinha subindo
do elemento tocado e uma vibração curta no celular. O contador fica na barra da
sessão e zera a cada sessão nova.

A escala não é arbitrária: **recompensa o que gera dado**. Marcar é só a
escolha final. Riscar é o raciocínio, e é a eliminação que alimenta o peso da
tentativa, o crédito parcial no erro e a matriz de confusão. O gesto mais
valioso para o diagnóstico é o que paga mais.

Três salvaguardas para a recompensa não corromper o próprio dado:

- **cada alternativa rende cada gesto uma única vez por questão.** Marcar e
  desmarcar em looping não farma ponto nenhum;
- **não existe ponto por acertar.** O sistema paga pelo processo, nunca pelo
  resultado — premiar acerto empurraria você a responder só o que já domina,
  que é o oposto do que a trilha quer;
- **o modo simulado não pontua.** Ali a regra é "nada além do que existe no
  papel" (seção 8), e plaquinha na tela é exatamente algo que não existe na
  prova.

→ `PONTOS` em `web/app.js`

## Onde o LLM aparece (e onde não)

Dois lugares, os dois fora da rotina diária e os dois opcionais:

- botão **Sugerir tags** na tela de importar;
- botão **Escrever relatório em prosa** no painel.

Resolver, corrigir, calcular domínio e ordenar a lista de hoje não passam por
lá. O painel mostra um contador de chamadas a LLM — se ele não mexer depois de
uma sessão, o critério de aceitação nº 4 está cumprido de fato, não de palavra.

Para ligar: `pip install anthropic` e `set ANTHROPIC_API_KEY=...`. Sem isso os
botões somem e nenhuma chamada é tentada.

## O que está deliberadamente de fora

Fases 6, 7 e 8 da spec, que só começam depois das três semanas:

- calibração (excesso de confiança × subestimação);
- trilha e repetição espaçada (a tabela `agendamento` nasce vazia de propósito);
- trilha e repetição espaçada.

A **etiquetagem de distratores** (fase 8) foi antecipada: o motivo de adiar era
o custo manual, e como as 252 questões foram geradas aqui, o custo não existia.
528 dos 756 distratores (70%) carregam `tag_concorrente_id`. Os outros 30% são
afirmações falsas sem conceito por trás, e ficam `NULL` de propósito.

## O banco de 252 questões

`questoes/*.json` traz **uma questão por folha diagnóstica** — 252 conceitos,
252 questões, todas conceituais e diretas, sem pegadinha. Depois de responder
todas uma vez, nenhuma folha fica cinza e os 52 nós internos ganham domínio
estimado por herança, sem nenhuma questão própria.

    python carregar_questoes.py              # carrega
    python carregar_questoes.py --so-checar   # só audita

O carregador **se recusa a gravar** um lote com defeito. Ele checa:

- **pista formal** (seção 6.3): se a alternativa correta for mais que 1,5× a
  mediana dos distratores, a questão entrega a resposta pela forma. Zero
  questões do banco disparam a flag, e a correta é a mais longa em exatamente
  25% delas — o mesmo que o acaso daria;
- **balanço de letras**: a correta é rotacionada pela posição do item, o que dá
  63 A, 63 B, 63 C, 63 D. Não há padrão de letra para ler;
- ids de tag inexistentes, enunciados duplicados, alternativas repetidas,
  mais de 3 tags por questão.

Dentro dos arquivos, a correta vem **sempre primeiro**, só para facilitar a
revisão humana. A rotação acontece na hora de gravar.

Tags secundárias aparecem em apenas 42 das 252, e só onde a questão exige de
fato discriminar um par que se confunde (colênquima × esclerênquima, ovário
súpero × ínfero, epígea × hipógea). A razão está medida: em simulação, marcar
secundária ao acaso **não melhora** a atribuição — só aumenta a cobertura.
Onde a secundária não é genuinamente exigida, ela só confunde a evidência.

## Triagem: prioridade para assunto sem dado

No modo **triagem** a seleção muda de critério. Estudo serve tag fraca; triagem
serve tag **virgem** — assunto fraco já tem diagnóstico, assunto sem dado não
tem nada, e medir o que nunca foi medido rende muito mais por questão. É o
"início frio" da seção 10.

A ordem de desempate, nessa ordem:

1. tag principal com peso bruto abaixo do mínimo (nunca medida);
2. subárea de nível 2 menos visitada **nesta sessão** — varre em largura em vez
   de afundar num ramo só;
3. questão menos exposta;
4. sorteio.

Só entram questões com `serve_para_triagem = 1`. A tela marca **assunto novo**
quando a questão inaugura uma tag.

O ramo transversal está declarado no YAML com `transversal: true` e **não conta
como subárea** na varredura: suas folhas já vivem num ramo temático, e é lá que
a visita precisa ser marcada. Sem isso, responder uma questão de raiz axial
contava como "Monocotiledônea × eudicotiledônea" e deixava "Raiz" parecendo
intocada. Numa simulação, 40 questões de triagem cobriram 28 subáreas temáticas,
no máximo 2 por subárea.

**O que ainda falta da triagem completa (fase 6):** descer ao nível 3 só no ramo
onde você cai, parar em 3–5 questões por tópico, e a calibração. Hoje ela varre
o nível 2 inteiro sem podar.

## Matriz de confusão (v0.3)

`tc` no JSON responde **"qual conceito faria esta alternativa ser a certa?"** —
não "que palavra o texto usa". Em "O procâmbio dá origem a quais tecidos?", o
distrator *"Epiderme e cutícula"* aponta para `bio.protoderme`: quem marca isso
confunde procâmbio com protoderme. É isso que a v0.2 não conseguia dizer.

O painel mostra **"você troca X por Y"**, e a força não é contagem bruta:

    força = vezes que escolheu Y / (escolheu Y + riscou Y corretamente)

Escolher Y é confusão; riscar Y na mesma posição é discriminação demonstrada.
Dois erros em duas oportunidades é muito diferente de dois em doze. Questões de
enunciado negativo ficam fora — ali a correta é a afirmação falsa e não existe
conceito concorrente (seção 6.3). → `MIN_CONFUSAO`

**Limite que vale saber:** o banco tem **uma questão por conceito**, então um par
só atinge o mínimo de 2 ocorrências depois que você reencontra a questão. Numa
simulação, a matriz só fica legível na terceira passada pelo banco. Se quiser
confusão medida antes disso, é importar mais questões nas tags que já apareceram
vermelhas — que é exatamente a estratégia da seção 6.2.

## Onde o buraco é do ramo, não da folha

Abaixo de "o que eu estudo hoje", o painel traz **"Conteúdos-pai mais
deficitários"**: a mesma evidência, um nível acima da árvore. A lista de cima
devolve folhas — que é o que se estuda; esta devolve ramos — que é o que se
decide. Doze folhas fracas espalhadas por seis ramos e doze dentro de um só
pedem coisas diferentes, e a lista de folhas não distingue os dois casos.

A porcentagem do ramo é herdada das folhas, com o peso decaindo a cada nível de
distância — não é média de porcentagens nem taxa de acerto. Ao lado dela vem
`fracas/medidas`, que é o que separa "o ramo caiu" de "uma folha caiu", e quantas
folhas do ramo ainda estão sem dado (o número só fala das medidas).

**A setinha abre as tags em si**, da mais fraca para a mais forte, cada uma com o
cru (`acertos/tentativas`) e um **estudar** que já começa a sessão restrita
àquela tag. Um ramo só entra na lista com pelo menos duas folhas respondidas:
com uma só, "conteúdo-pai deficitário" seria a mesma folha com outro nome
(`MIN_FOLHAS_PARA_PAI` em `config.py`, junto de `PESO_FOLHAS_FRACAS` e
`PAIS_NO_PAINEL`).

## Primeiros passos sugeridos

1. `python carregar_questoes.py` e depois `python app.py`
2. Aba **Resolver**, modo estudo. Dez por dia.
3. Aba **Painel** no fim da semana. Se o topo da lista te surpreender, a
   hipótese central da v0.2 se sustentou. Leia as duas listas na ordem: o ramo
   diz onde cavar, a setinha diz qual tag abrir.
4. Quando o banco gerado se esgotar, aba **Importar**: cole lotes de apostila.
   Priorize "assinale a incorreta" e V/F, e marque a procedência — questão de
   prova antiga tem gabarito validado por terceiros, a gerada não.

## Como foi feito

Desenvolvido com o Claude Code, a partir de uma especificação que eu escrevi: o que o sistema precisa medir, os critérios de aceitação e o que fica de fora. As 252 questões do banco foram geradas com IA e revisadas pelo carregador automático.

## Licença

MIT © 2026 Arthur Schuck
