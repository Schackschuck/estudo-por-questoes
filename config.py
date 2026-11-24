"""
Todas as constantes numericas do sistema vivem aqui.

A especificacao v0.2 (secao 16) avisa: "Nenhum numero esta definido em lugar
nenhum desta especificacao - e onde o projeto vai empacar se nao for
enfrentado na fase 4." Entao todo numero arbitrado esta neste arquivo, com a
justificativa ao lado. Mudar um numero e editar uma linha, nunca cacar codigo.
"""

# ---------------------------------------------------------------- banco / app
ARQUIVO_BANCO = "estudo.db"
ARQUIVO_TAXONOMIA = "taxonomia/biologia.yaml"
PORTA = 8765

# ------------------------------------------------- decisoes pendentes da v0.2
# (secao 16 - "precisam da minha resposta antes da fase 1")

# 1. Eliminar alternativa e obrigatorio no modo estudo?
#    DECIDIDO: opcional. O criterio de sucesso mais dificil da prova de
#    conceito e "eu continuei usando na terceira semana"; atrito obrigatorio
#    na rotina diaria e o jeito mais rapido de perder isso. No modo triagem,
#    que e ocasional, a instrumentacao completa continua sendo pedida.
ELIMINACAO_OBRIGATORIA = {"triagem": False, "estudo": False, "simulado": False}

# 2. Quantas questoes por tag sao "cobertura suficiente"?
#    DECIDIDO por peso acumulado, nao por contagem de questoes, porque uma
#    questao secundaria vale 0,5 e uma heranca de ancestral vale menos ainda.
#    Abaixo de PESO_MINIMO_SINAL a tag e cinza (sem dado).
#    Entre sinal e medida, o painel mostra o nivel como provisorio.
PESO_MINIMO_SINAL = 1.0    # menos que isso: cinza, nunca vermelho
PESO_MEDIDA = 5.0          # a partir daqui a tag conta como "medida"

# 3. "Nao preciso desse conteudo" exclui a tag isolada ou o ramo inteiro?
#    DECIDIDO: o ramo inteiro abaixo dela. Na pratica ninguem descarta um
#    conceito solto - descarta um assunto. Sempre reversivel no painel.
EXCLUIR_RAMO_INTEIRO = True

# 4. Peso da tag secundaria.
#    0,5 e chute assumido. Fica aqui sozinho para ser recalibrado com o
#    proprio dado depois das tres semanas (ver modelo.calibrar_peso_secundaria).
PESO_PRINCIPAL = 1.0
PESO_SECUNDARIA = 0.5
MAX_TAGS_POR_QUESTAO = 3

# --------------------------------------------------------- modelo do aluno
# Heranca pela arvore: um acerto em bio.procambio e evidencia mais fraca para
# bio.meristemas e mais fraca ainda para bio.histologia_vegetal.
DECAIMENTO_ANCESTRAL = 0.6          # peso = peso_tag * DECAIMENTO ** distancia
DISTANCIA_MAXIMA_ANCESTRAL = 3

# Peso decrescente por exposicao: parte do acerto na 2a vez e reconhecimento.
PESO_POR_EXPOSICAO = {0: 1.0, 1: 0.6, 2: 0.3}
PESO_EXPOSICAO_MINIMO = 0.15        # 4a vez em diante

# Credito pela eliminacao (o raciocinio mostrado, nao so a escolha final).
# Riscar um distrator E conhecimento demonstrado: voce sabia que aquilo estava
# errado. Sem tag no distrator (isso e v0.3), nao da para dizer QUAL conceito
# voce discriminou -- mas da para dizer QUANTO, e isso muda o peso da
# evidencia sobre as tags que a questao ja tem.
#   fracao = distratores corretamente riscados / total de distratores
#   (zero se voce riscou a alternativa CERTA: ali nao houve discriminacao)
BONUS_ELIMINACAO = 0.5      # acerto com eliminacao completa pesa 1,5x em vez de 1
CREDITO_ELIMINACAO = 0.3    # erro depois de reduzir o campo ganha credito parcial

# Riscar a alternativa CERTA e errar nao e ausencia de conhecimento, e uma
# concepcao errada forte o bastante para voce agir sobre ela. A tentativa
# carrega dois fatos independentes -- escolheu errado E rejeitou o certo --
# entao vale por duas observacoes. Nao existe "dominio negativo": nivel e uma
# probabilidade, o piso e zero. O que da para dizer e "errado COM CONVICCAO",
# e isso se escreve como mais peso, nunca como valor negativo.
PENALIDADE_RISCAR_CERTA = 1.0   # peso x2

# Prior do nivel de dominio: Beta(1,1) = Laplace. Com n=0 o nivel da 0,5 e a
# incerteza e maxima - por isso o painel usa o peso, e nao o nivel, para
# decidir se a tag e cinza.
PRIOR_ACERTOS = 1.0
PRIOR_TOTAL = 2.0

# Limiares de tratamento (secao 16, ultimo item das pendencias).
# Usam o LIMITE SUPERIOR do intervalo, para nunca chamar de fraca uma tag
# que so tem pouco dado.
LIMIAR_FRACA = 0.60                 # teto do intervalo abaixo disso = fraca
LIMIAR_FORTE = 0.80                 # piso do intervalo acima disso = dominada

# Atribuicao por cruzamento: quando uma questao multi-tag e errada, a
# evidencia negativa se concentra na tag cujo historico ja e mais fraco.
# 0 = divide igual (desliga), 1 = concentra proporcional a (1 - nivel).
FORCA_ATRIBUICAO_CRUZADA = 1.0

# Matriz de confusao (v0.3): quantas vezes o par precisa aparecer para
# "voce troca X por Y" deixar de ser coincidencia.
MIN_CONFUSAO = 2

# Alerta de tags coladas.
CORRELACAO_COLADA = 0.9
MIN_COOCORRENCIA = 3                # so avisa se o par ja apareceu 3+ vezes

# ------------------------------------------------------ classificacao de erro
SEGUNDOS_RESPOSTA_RAPIDA = 8.0      # abaixo disso a resposta foi reflexo
SEGUNDOS_SUSPEITA_CHUTE = 4.0       # sem eliminacao e sem meta-resposta

# --------------------------------------------------------- quarentena (sec 7)
QUARENTENA_MIN_TENTATIVAS = 4
QUARENTENA_TAXA_ERRO = 0.75         # erro da questao...
QUARENTENA_FOLGA_SOBRE_TAG = 0.30   # ...tanto acima da taxa media da tag

# ------------------------------------------------------- pista formal (sec 6.3)
PISTA_FORMAL_RAZAO = 1.5            # correta 50% mais longa que a mediana

# -------------------------------------------- painel: conteudos-pai fracos
# O painel lista folhas ("o que eu estudo hoje"); estes numeros governam a
# lista irma, a dos NOS INTERNOS mais deficitarios - onde o buraco e do ramo
# e nao de uma folha solta.
# Com uma folha medida so, "conteudo pai deficitario" e a mesma folha com
# outro nome; a partir de duas a afirmacao passa a ser sobre o ramo.
MIN_FOLHAS_PARA_PAI = 2
# Quanto a proporcao de folhas fracas empurra o ramo para cima na fila.
# 1,0 = ramo com todas as folhas fracas vale o dobro de um sem nenhuma.
PESO_FOLHAS_FRACAS = 1.0
# Quantos ramos o painel mostra. Lista longa vira parede e ninguem le.
PAIS_NO_PAINEL = 8
