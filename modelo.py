"""
Modelo do aluno (seção 11). Roda localmente, por regras e aritmética.
Nenhuma chamada a LLM aqui — nem uma.

O que este módulo implementa, na ordem da especificação:
  - domínio por tag COM incerteza (três acertos não valem trinta);
  - peso decrescente por exposição;
  - agregação por ancestral com decaimento pela distância;
  - evidência por peso, não por contagem bruta;
  - atribuição por cruzamento (seção 6.1);
  - alerta de tags coladas;
  - distinção entre tag sem dado e tag fraca;
  - classificação do tipo de erro.
"""
import math
from collections import defaultdict

import config
import db

# --------------------------------------------------------------------------
# Coleta da evidência
# --------------------------------------------------------------------------

def _peso_exposicao(n: int) -> float:
    return config.PESO_POR_EXPOSICAO.get(n, config.PESO_EXPOSICAO_MINIMO)


def _tentativas_validas(con):
    """
    Tentativas que contam. Ficam de fora:
      - meta-resposta 'já fiz essa' (resultado='descartada');
      - questões em quarentena ou aposentadas — retroativamente (seção 7).
    """
    return con.execute(
        """SELECT t.*, q.pista_formal
             FROM tentativa t
             JOIN questao q ON q.id = t.questao_id
            WHERE t.resultado IN ('acerto','erro')
              AND q.estado = 'ativa'
         ORDER BY t.id"""
    ).fetchall()


def _tags_por_questao(con):
    m = defaultdict(list)
    for r in con.execute("SELECT questao_id, tag_id, papel, peso FROM questao_tag"):
        m[r["questao_id"]].append((r["tag_id"], r["peso"]))
    return m


def _ancestrais(con):
    m = defaultdict(list)
    for r in con.execute("SELECT tag_id, ancestral_id, distancia FROM tag_ancestral"):
        m[r["tag_id"]].append((r["ancestral_id"], r["distancia"]))
    return m


def discriminacao(con, tentativa_id=None) -> dict:
    """
    Quanto raciocínio cada tentativa mostrou, derivado de `evento` — "a tabela
    que alimenta o modelo do aluno" (seção 12). Não é coluna guardada: se a
    fórmula mudar, o histórico inteiro se recalcula sozinho.

    Devolve tentativa_id -> fração dos distratores corretamente eliminados.
    Se você riscou a alternativa CERTA, a fração é 0: ali não houve
    discriminação, houve um equívoco declarado — e equívoco não ganha bônus.
    """
    filtro = "AND e.tentativa_id = ?" if tentativa_id else ""
    params = (tentativa_id,) if tentativa_id else ()
    # eliminou e depois desmarcou não conta: vale o saldo líquido
    liquidas = con.execute(
        f"""SELECT e.tentativa_id, e.alternativa_id,
                   SUM(CASE e.tipo WHEN 'eliminou' THEN 1
                                   WHEN 'desmarcou_eliminacao' THEN -1
                                   ELSE 0 END) net
              FROM evento e
             WHERE e.alternativa_id IS NOT NULL {filtro}
          GROUP BY e.tentativa_id, e.alternativa_id""", params).fetchall()
    if not liquidas:
        return {}

    correta = {r["id"]: r["correta"] for r in con.execute(
        "SELECT id, correta FROM alternativa")}
    distratores = {r["questao_id"]: r["n"] - 1 for r in con.execute(
        "SELECT questao_id, COUNT(*) n FROM alternativa GROUP BY questao_id")}
    questao = {r["id"]: r["questao_id"] for r in con.execute(
        "SELECT id, questao_id FROM tentativa")}

    acum = {}
    for r in liquidas:
        if r["net"] <= 0 or r["alternativa_id"] not in correta:
            continue
        certas, riscou_a_certa = acum.get(r["tentativa_id"], (0, False))
        if correta[r["alternativa_id"]]:
            riscou_a_certa = True
        else:
            certas += 1
        acum[r["tentativa_id"]] = (certas, riscou_a_certa)

    saida = {}
    for tid, (certas, riscou_a_certa) in acum.items():
        nd = distratores.get(questao.get(tid), 0)
        saida[tid] = {
            "fracao": 0.0 if (riscou_a_certa or not nd) else min(1.0, certas / nd),
            "riscou_a_correta": riscou_a_certa,
        }
    return saida


def multiplicador(fracao: float, riscou_a_correta=False, errou=False) -> float:
    """
    Quanto esta tentativa pesa, pelo raciocínio que ela mostrou.

    Riscar a certa e errar pesa MAIS, não menos: são dois fatos independentes
    (escolheu errado e rejeitou o certo), e o peso extra empurra o nível para
    baixo sem precisar de um "domínio negativo", que não existiria — nível é
    probabilidade, o piso é zero. O que o modelo registra é "errado com
    convicção", que é diferente de "não sabia".
    """
    if riscou_a_correta and errou:
        return 1.0 + config.PENALIDADE_RISCAR_CERTA
    return 1.0 + config.BONUS_ELIMINACAO * fracao


def _espalhar(pesos_diretos, ancestrais):
    """
    Herança pela árvore. Um ancestral recebe o MAIOR entre os pesos que suas
    descendentes na questão lhe mandam — nunca a soma, senão uma questão com
    três tags do mesmo ramo contaria três vezes para o pai.
    """
    total = dict(pesos_diretos)
    herdado = {}
    for tag, peso in pesos_diretos.items():
        for anc, dist in ancestrais.get(tag, ()):
            contrib = peso * (config.DECAIMENTO_ANCESTRAL ** dist)
            if contrib > herdado.get(anc, 0.0):
                herdado[anc] = contrib
    for anc, peso in herdado.items():
        if anc not in total:          # tag etiquetada diretamente manda mais
            total[anc] = peso
    return total


def _nivel(acertos: float, total: float) -> tuple:
    """Beta(1,1): com pouco dado o nível puxa para 0,5 e a incerteza é grande."""
    n = total + config.PRIOR_TOTAL
    nivel = (acertos + config.PRIOR_ACERTOS) / n
    incerteza = math.sqrt(max(nivel * (1 - nivel), 1e-9) / (n + 1))
    return nivel, incerteza


# --------------------------------------------------------------------------
# Cálculo de domínio
# --------------------------------------------------------------------------

def calcular(con) -> dict:
    """
    Recalcula o domínio de todas as tags do zero. Duas passadas:
      1ª — evidência dividida pelo papel, para estimar um nível-base;
      2ª — atribuição por cruzamento: numa questão multi-tag errada, a
           evidência negativa se concentra na tag cujo histórico já é mais
           fraco, em vez de se dividir igualmente (seção 6.1).
    """
    tentativas = _tentativas_validas(con)
    tags_q = _tags_por_questao(con)
    anc = _ancestrais(con)
    disc = discriminacao(con)

    def passada(nivel_base):
        acertos = defaultdict(float)
        totais = defaultdict(float)
        brutos = defaultdict(float)   # peso ANTES da redistribuição = cobertura
        for t in tentativas:
            tags = tags_q.get(t["questao_id"], [])
            if not tags:
                continue
            # O que você riscou conta duas vezes: dá mais peso à tentativa
            # (você mostrou o raciocínio) e, num erro, dá crédito parcial
            # (você reduziu o campo, mesmo sem fechar).
            d = disc.get(t["id"]) or {}
            fracao = d.get("fracao", 0.0)
            errou = t["resultado"] == "erro"
            pe = _peso_exposicao(t["exposicoes_anteriores"] or 0) * multiplicador(
                fracao, d.get("riscou_a_correta", False), errou)
            diretos = {tag: peso * pe for tag, peso in tags}
            for tag, peso in _espalhar(diretos, anc).items():
                brutos[tag] += peso

            credito = 1.0 if not errou else config.CREDITO_ELIMINACAO * fracao
            if errou and len(diretos) > 1 and nivel_base is not None:
                soma = sum(diretos.values())
                fraqueza = {tag: 1.0 - nivel_base.get(tag, 0.5) for tag in diretos}
                sf = sum(fraqueza.values()) or 1.0
                for tag in diretos:
                    uniforme = diretos[tag] / soma
                    concentrado = fraqueza[tag] / sf
                    f = config.FORCA_ATRIBUICAO_CRUZADA
                    diretos[tag] = soma * ((1 - f) * uniforme + f * concentrado)

            for tag, peso in _espalhar(diretos, anc).items():
                totais[tag] += peso
                acertos[tag] += peso * credito
        return acertos, totais, brutos

    acertos, totais, _ = passada(None)
    base = {tag: _nivel(acertos[tag], totais[tag])[0] for tag in totais}
    acertos, totais, brutos = passada(base)

    agora = db.agora()
    con.execute("DELETE FROM dominio_tag")
    linhas = []
    for tag, total in totais.items():
        nivel, incerteza = _nivel(acertos[tag], total)
        linhas.append((tag, nivel, incerteza, total, brutos[tag], agora))
    con.executemany(
        "INSERT INTO dominio_tag(tag_id,nivel,incerteza,peso_total,peso_bruto,"
        "atualizado_em) VALUES(?,?,?,?,?,?)", linhas)
    con.commit()
    return {"tags_com_dado": len(linhas), "tentativas": len(tentativas)}


def situacao(nivel: float, incerteza: float, peso: float, peso_bruto=None) -> str:
    """
    Nunca apresentar ausência de evidência como evidência de fraqueza:
    abaixo do peso mínimo a tag é 'sem_dado' (cinza), nunca 'fraca'.

    A decisão de "tem dado?" usa o peso BRUTO, de antes da atribuição por
    cruzamento. Sem isso, uma tag medida diretamente cujo erro foi atribuído
    à colega de questão cai abaixo do limiar e aparece como cinza — o sistema
    passaria a confundir "não medi" com "medi e a culpa foi de outra tag".
    """
    if (peso if peso_bruto is None else peso_bruto) < config.PESO_MINIMO_SINAL:
        return "sem_dado"
    teto = min(1.0, nivel + 1.96 * incerteza)
    piso = max(0.0, nivel - 1.96 * incerteza)
    if teto < config.LIMIAR_FRACA:
        return "fraca"
    if piso > config.LIMIAR_FORTE:
        return "dominada"
    return "parcial"


# --------------------------------------------------------------------------
# Classificação da tentativa (roda no momento de gravar)
# --------------------------------------------------------------------------

def classificar(con, questao_id, resultado, meta_resposta, confianca,
                tempo_total, n_eliminacoes, riscou_a_correta=False) -> dict:
    """Tipo de erro, suspeita de chute e domínio ilusório (seção 11)."""
    out = {"tipo_de_erro": None, "suspeita_chute": 0, "dominio_ilusorio": 0}
    rapido = (tempo_total or 999) < config.SEGUNDOS_RESPOSTA_RAPIDA

    if meta_resposta == "nao_faco_ideia":
        out["tipo_de_erro"] = "lacuna"
        return out

    if resultado == "erro":
        r = con.execute(
            """SELECT AVG(d.nivel) AS n
                 FROM questao_tag qt LEFT JOIN dominio_tag d ON d.tag_id = qt.tag_id
                WHERE qt.questao_id = ? AND qt.papel = 'principal'""",
            (questao_id,)).fetchone()
        nivel = r["n"] if r and r["n"] is not None else 0.5
        # Risquei a certa: não é lacuna nem descuido, é concepção errada.
        if riscou_a_correta:
            out["tipo_de_erro"] = "equivoco"
            return out
        # Domino a tag, errei rápido e declarando certeza: descuido, sem tratamento.
        if nivel >= config.LIMIAR_FORTE and rapido and confianca == "certeza":
            out["tipo_de_erro"] = "descuido"
        else:
            out["tipo_de_erro"] = "conceitual"
        if (n_eliminacoes == 0 and not meta_resposta
                and (tempo_total or 999) < config.SEGUNDOS_SUSPEITA_CHUTE):
            out["suspeita_chute"] = 1
    elif resultado == "acerto":
        # Acertei sem eliminar nada, rápido, com baixa confiança: reteste espaçado.
        if n_eliminacoes == 0 and rapido and confianca in ("chute", "duvida_entre_duas"):
            out["dominio_ilusorio"] = 1
    return out


# --------------------------------------------------------------------------
# Quarentena automática (seção 7)
# --------------------------------------------------------------------------

def checar_quarentena(con, questao_id) -> str | None:
    """
    Sinal de defeito: a questão erra muito acima da média das tags dela, ou
    erro repetido onde declarei certeza enquanto acerto as outras da tag.
    Devolve o motivo se colocou em quarentena.
    """
    q = con.execute("SELECT estado FROM questao WHERE id=?", (questao_id,)).fetchone()
    if not q or q["estado"] != "ativa":
        return None
    t = con.execute(
        """SELECT COUNT(*) n,
                  SUM(resultado='erro') erros,
                  SUM(resultado='erro' AND confianca_declarada IN
                      ('certeza','eliminacao_completa')) erros_certos
             FROM tentativa WHERE questao_id=? AND resultado IN ('acerto','erro')""",
        (questao_id,)).fetchone()
    if not t["n"] or t["n"] < config.QUARENTENA_MIN_TENTATIVAS:
        return None
    taxa = (t["erros"] or 0) / t["n"]

    ref = con.execute(
        """SELECT COUNT(*) n, SUM(t.resultado='erro') erros
             FROM tentativa t
             JOIN questao_tag qt ON qt.questao_id = t.questao_id
            WHERE qt.tag_id IN (SELECT tag_id FROM questao_tag WHERE questao_id=?)
              AND t.questao_id <> ? AND t.resultado IN ('acerto','erro')""",
        (questao_id, questao_id)).fetchone()
    taxa_tag = (ref["erros"] or 0) / ref["n"] if ref["n"] else 0.0

    motivo = None
    if taxa >= config.QUARENTENA_TAXA_ERRO and taxa - taxa_tag >= config.QUARENTENA_FOLGA_SOBRE_TAG:
        motivo = (f"erro de {taxa:.0%} em {t['n']} tentativas, contra {taxa_tag:.0%} "
                  f"nas outras questões das mesmas tags — gabarito suspeito")
    elif (t["erros_certos"] or 0) >= 2 and taxa_tag < 0.5:
        motivo = (f"{t['erros_certos']} erros declarando certeza, enquanto as outras "
                  f"questões das mesmas tags vão bem — gabarito suspeito")

    if motivo:
        con.execute("UPDATE questao SET estado='quarentena', motivo_quarentena=? WHERE id=?",
                    (motivo, questao_id))
        con.commit()
    return motivo


# --------------------------------------------------------------------------
# Tags coladas (seção 6.1)
# --------------------------------------------------------------------------

def tags_coladas(con) -> list:
    """
    Duas tags que só aparecem juntas são indistinguíveis para sempre.
    Coeficiente phi sobre a matriz de incidência conceito×questão.
    """
    porq = defaultdict(set)
    for r in con.execute("SELECT questao_id, tag_id FROM questao_tag"):
        porq[r["questao_id"]].add(r["tag_id"])
    n = len(porq)
    if n < config.MIN_COOCORRENCIA:
        return []

    conta = defaultdict(int)
    par = defaultdict(int)
    for tags in porq.values():
        ts = sorted(tags)
        for t in ts:
            conta[t] += 1
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                par[(a, b)] += 1

    nomes = {r["id"]: r["nome_canonico"] for r in con.execute("SELECT id,nome_canonico FROM tag")}
    saida = []
    for (a, b), n11 in par.items():
        if n11 < config.MIN_COOCORRENCIA:
            continue
        n10, n01 = conta[a] - n11, conta[b] - n11
        n00 = n - n11 - n10 - n01
        den = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
        if den == 0:
            continue
        phi = (n11 * n00 - n10 * n01) / den
        if phi >= config.CORRELACAO_COLADA:
            saida.append({
                "a": a, "b": b,
                "nome_a": nomes.get(a, a), "nome_b": nomes.get(b, b),
                "correlacao": round(phi, 3), "juntas_em": n11,
                "sugestao": f"importe uma questão que cobre '{nomes.get(a, a)}' sem "
                            f"'{nomes.get(b, b)}' — do jeito que está, as duas nunca "
                            f"serão distinguidas.",
            })
    return sorted(saida, key=lambda x: -x["correlacao"])


# --------------------------------------------------------------------------
# Matriz de confusão (v0.3, seção 6.2)
# --------------------------------------------------------------------------

def confusao(con, limite=12) -> list:
    """
    "Você troca X por Y" — o que a v0.2 não conseguia responder.

    Só existe porque o distrator agora carrega `tag_concorrente_id`: sem isso,
    marcar a alternativa errada informava que você errou a tag, nunca COM O QUE
    você confundiu.

    A força não é contagem bruta. Escolher Y é confusão, mas RISCAR Y na mesma
    posição é o oposto: é discriminação demonstrada. Então a força mede
    "quando Y esteve em jogo, quantas vezes você caiu nele" — dois erros em
    duas oportunidades é muito diferente de dois erros em doze.

    Questões de enunciado negativo ficam de fora: ali a alternativa certa é a
    afirmação falsa, e não existe conceito concorrente (seção 6.3).
    """
    liquidas = {(r["tentativa_id"], r["alternativa_id"]) for r in con.execute(
        """SELECT tentativa_id, alternativa_id,
                  SUM(CASE tipo WHEN 'eliminou' THEN 1
                                WHEN 'desmarcou_eliminacao' THEN -1
                                ELSE 0 END) net
             FROM evento WHERE alternativa_id IS NOT NULL
         GROUP BY tentativa_id, alternativa_id""") if r["net"] > 0}

    alts = defaultdict(list)
    for a in con.execute(
            """SELECT id, questao_id, letra, tag_concorrente_id FROM alternativa
                WHERE tag_concorrente_id IS NOT NULL"""):
        alts[a["questao_id"]].append(a)

    caiu, distinguiu = defaultdict(int), defaultdict(int)
    for t in con.execute(
            """SELECT t.id, t.resultado, t.alternativa_escolhida, t.questao_id,
                      qt.tag_id AS principal
                 FROM tentativa t
                 JOIN questao q ON q.id = t.questao_id
                 JOIN questao_tag qt ON qt.questao_id = t.questao_id
                                    AND qt.papel = 'principal'
                WHERE t.resultado IN ('acerto','erro') AND q.estado = 'ativa'
                  AND q.enunciado_negativo = 0"""):
        for a in alts.get(t["questao_id"], ()):
            y = a["tag_concorrente_id"]
            if y == t["principal"]:
                continue
            par = (t["principal"], y)
            if t["resultado"] == "erro" and a["letra"] == t["alternativa_escolhida"]:
                caiu[par] += 1
            elif (t["id"], a["id"]) in liquidas:
                distinguiu[par] += 1

    nomes = {r["id"]: r["nome_canonico"] for r in con.execute(
        "SELECT id, nome_canonico FROM tag")}
    saida = []
    for (de, para), n in caiu.items():
        if n < config.MIN_CONFUSAO:
            continue
        d = distinguiu.get((de, para), 0)
        nd, np_ = nomes.get(de, de), nomes.get(para, para)
        saida.append({
            "de": de, "para": para, "nome_de": nd, "nome_para": np_,
            "caiu": n, "distinguiu": d, "forca": round(n / (n + d), 3),
            "mutua": (para, de) in caiu,
            "frase": f"Quando erra {nd}, você marca {np_} — {n}× em "
                     f"{n + d} oportunidade(s).",
        })
    saida.sort(key=lambda x: (-x["forca"], -x["caiu"]))
    return saida[:limite]


# --------------------------------------------------------------------------
# "O que eu estudo hoje" (seção 11, saídas)
# --------------------------------------------------------------------------

def _por_que(r, cru) -> str:
    """
    Explica o número quando não há causa mais específica. Precisa deixar claro
    que 'nivel' NÃO é taxa de acerto: é uma estimativa de domínio que parte de
    50% e só se afasta disso conforme a evidência se acumula (prior Beta(1,1)).
    Um acerto em uma questão dá 67%, não 100% — é a regra "três acertos não
    valem trinta" da seção 11.
    """
    if cru and cru[1]:
        return (f"acertou {cru[0]} de {cru[1]} questão(ões) desta tag; domínio "
                f"estimado em {r['nivel']:.0%} porque a estimativa parte de 50% "
                f"e só se afasta com evidência (peso {r['peso_total']:.1f})")
    return (f"sem questão própria: domínio estimado em {r['nivel']:.0%} herdado "
            f"das tags-filhas (peso {r['peso_total']:.1f})")


def fraquezas(con, limite=12) -> list:
    """Lista ordenada de fraquezas com causa provável. Sem LLM."""
    excluidas = {r["tag_id"] for r in con.execute("SELECT tag_id FROM escopo_tag")}
    filhos = defaultdict(list)
    for r in con.execute("SELECT tag_id, ancestral_id FROM tag_ancestral WHERE distancia=1"):
        filhos[r["ancestral_id"]].append(r["tag_id"])

    dom = {}
    for r in con.execute(
            """SELECT d.*, t.nome_canonico, t.diagnostica
                 FROM dominio_tag d JOIN tag t ON t.id = d.tag_id"""):
        dom[r["tag_id"]] = r

    # Contagem crua: quantas questões DESTA tag você respondeu e acertou.
    # Não é ponderada nem herdada — existe só para o painel poder mostrar o
    # dado bruto ao lado da estimativa, que são coisas diferentes.
    cru = {r["tag_id"]: (r["acertos"] or 0, r["n"]) for r in con.execute(
        """SELECT qt.tag_id, SUM(t.resultado='acerto') acertos, COUNT(*) n
             FROM tentativa t
             JOIN questao_tag qt ON qt.questao_id = t.questao_id
             JOIN questao q ON q.id = t.questao_id
            WHERE t.resultado IN ('acerto','erro') AND q.estado = 'ativa'
         GROUP BY qt.tag_id""")}

    itens = []
    for tid, r in dom.items():
        if tid in excluidas or not r["diagnostica"]:
            continue
        sit = situacao(r["nivel"], r["incerteza"], r["peso_total"], r["peso_bruto"])
        if sit in ("sem_dado", "dominada"):
            continue
        # Confiabilidade cresce com o peso acumulado: uma tag com muito dado
        # e nível baixo sobe na frente de uma com pouco dado e nível baixo.
        confiabilidade = r["peso_total"] / (r["peso_total"] + config.PESO_MEDIDA)
        prioridade = (1 - r["nivel"]) * confiabilidade

        causa = []
        fracas = [f for f in filhos.get(tid, [])
                  if f in dom and situacao(dom[f]["nivel"], dom[f]["incerteza"],
                                           dom[f]["peso_total"],
                                           dom[f]["peso_bruto"]) == "fraca"]
        if len(fracas) >= 2:
            prioridade *= 1.25
            causa.append("as filhas caem juntas: a falha provável é no conceito-pai")
        if tid.startswith("bio.cd_") or any(
                a["ancestral_id"] == "bio.caracteres_diagnosticos"
                for a in con.execute("SELECT ancestral_id FROM tag_ancestral WHERE tag_id=?", (tid,))):
            causa.append("é par de confusão: você troca os caracteres diagnósticos")
        ilusorio = con.execute(
            """SELECT COUNT(*) n FROM tentativa t
                 JOIN questao_tag qt ON qt.questao_id = t.questao_id
                WHERE qt.tag_id=? AND t.dominio_ilusorio=1""", (tid,)).fetchone()["n"]
        if ilusorio:
            causa.append(f"{ilusorio} acerto(s) sem eliminação e com baixa confiança: "
                         f"domínio ilusório, pede reteste espaçado")
        equivocos = con.execute(
            """SELECT COUNT(*) n FROM tentativa t
                 JOIN questao_tag qt ON qt.questao_id = t.questao_id
                WHERE qt.tag_id=? AND t.tipo_de_erro='equivoco'""", (tid,)).fetchone()["n"]
        if equivocos:
            prioridade *= 1.2
            causa.append(f"{equivocos}× você riscou a alternativa certa: não é "
                         f"lacuna, é concepção errada — volte ao material antes "
                         f"de fazer mais questões")
        lacunas = con.execute(
            """SELECT COUNT(*) n FROM tentativa t
                 JOIN questao_tag qt ON qt.questao_id = t.questao_id
                WHERE qt.tag_id=? AND t.tipo_de_erro='lacuna'""", (tid,)).fetchone()["n"]
        if lacunas:
            causa.append(f"{lacunas}× 'não faço ideia': volte ao material, "
                         f"não a mais questões")
        pista = con.execute(
            """SELECT SUM(t.resultado='acerto' AND q.pista_formal=1) ap,
                      SUM(t.resultado='erro' AND q.pista_formal=0) eo
                 FROM tentativa t JOIN questao q ON q.id=t.questao_id
                 JOIN questao_tag qt ON qt.questao_id = t.questao_id
                WHERE qt.tag_id=?""", (tid,)).fetchone()
        if (pista["ap"] or 0) >= 1 and (pista["eo"] or 0) >= 1:
            causa.append("acertou a questão com pista formal e errou as outras da tag: "
                         "leitura de padrão de prova, não domínio")

        itens.append({
            "tag_id": tid,
            "nome": r["nome_canonico"],
            "nivel": round(r["nivel"], 3),
            "incerteza": round(r["incerteza"], 3),
            "peso": round(r["peso_total"], 2),
            "situacao": sit,
            "medida": r["peso_total"] >= config.PESO_MEDIDA,
            "prioridade": round(prioridade, 4),
            "acertos": cru.get(tid, (0, 0))[0],
            "tentativas": cru.get(tid, (0, 0))[1],
            "justificativa": causa or [_por_que(r, cru.get(tid))],
        })
    itens.sort(key=lambda x: -x["prioridade"])
    return itens[:limite]


def calibrar_peso_secundaria(con) -> dict:
    """
    Decisão pendente nº 4 da seção 16: 0,5 é chute. Depois de três semanas,
    isto devolve o número que o próprio dado sugere — a razão entre a taxa de
    erro quando a tag é secundária e quando ela é principal.
    """
    r = con.execute(
        """SELECT qt.papel,
                  AVG(CASE WHEN t.resultado='erro' THEN 1.0 ELSE 0.0 END) taxa,
                  COUNT(*) n
             FROM tentativa t JOIN questao_tag qt ON qt.questao_id = t.questao_id
            WHERE t.resultado IN ('acerto','erro')
         GROUP BY qt.papel""").fetchall()
    d = {x["papel"]: x for x in r}
    if "principal" not in d or "secundaria" not in d:
        return {"suficiente": False, "atual": config.PESO_SECUNDARIA,
                "motivo": "ainda não há tentativas nos dois papéis"}
    if min(d["principal"]["n"], d["secundaria"]["n"]) < 30:
        return {"suficiente": False, "atual": config.PESO_SECUNDARIA,
                "motivo": f"n insuficiente ({d['principal']['n']} principais, "
                          f"{d['secundaria']['n']} secundárias; precisa de 30 de cada)"}
    tp = d["principal"]["taxa"] or 1e-6
    sugerido = round(min(1.0, (d["secundaria"]["taxa"] or 0) / tp), 2)
    return {"suficiente": True, "atual": config.PESO_SECUNDARIA, "sugerido": sugerido,
            "motivo": f"erro em tag secundária é {sugerido:.2f}× o erro em tag "
                      f"principal; edite PESO_SECUNDARIA em config.py"}


# --------------------------------------------------------------------------
# Conteúdos-pai mais deficitários (a lista irmã de fraquezas(), um nível acima)
# --------------------------------------------------------------------------

def fraquezas_pai(con, limite=None) -> list:
    """
    Os nós INTERNOS mais deficitários, com as folhas medidas de cada ramo
    junto — o painel abre a lista na setinha.

    Por que existe: fraquezas() devolve folhas, e folha é o que se estuda,
    mas não é o que se decide. Doze folhas fracas espalhadas por seis ramos
    e doze folhas fracas dentro de um só pedem tratamentos diferentes, e a
    lista de folhas não distingue os dois casos.

    O número do pai não é recontado aqui: é o mesmo `dominio_tag` que a
    herança pela árvore já calculou em calcular() (peso decaindo com a
    distância). O que esta função acrescenta é a fila de prioridade e o
    recorte de quais folhas sustentam o número.

    Atenção: `tag.diagnostica` NÃO diz se a tag é folha — é quase sempre 1,
    inclusive em nó com filhos, e marca "vale medir". Quem é pai e quem é
    folha sai de tag_ancestral(distancia=1), a estrutura de verdade.
    """
    limite = config.PAIS_NO_PAINEL if limite is None else limite
    excluidas = {r["tag_id"] for r in con.execute("SELECT tag_id FROM escopo_tag")}

    descendentes = defaultdict(list)
    pais_diretos = defaultdict(list)
    com_filhos = set()
    for r in con.execute("SELECT tag_id, ancestral_id, distancia FROM tag_ancestral"):
        descendentes[r["ancestral_id"]].append(r["tag_id"])
        if r["distancia"] == 1:
            com_filhos.add(r["ancestral_id"])
            pais_diretos[r["tag_id"]].append(r["ancestral_id"])

    dom = {}
    for r in con.execute(
            """SELECT d.*, t.nome_canonico, t.transversal
                 FROM dominio_tag d JOIN tag t ON t.id = d.tag_id
                WHERE t.ausente_do_arquivo = 0"""):
        dom[r["tag_id"]] = r
    nomes = {r["id"]: r["nome_canonico"]
             for r in con.execute("SELECT id, nome_canonico FROM tag")}

    cru = {r["tag_id"]: (r["acertos"] or 0, r["n"]) for r in con.execute(
        """SELECT qt.tag_id, SUM(t.resultado='acerto') acertos, COUNT(*) n
             FROM tentativa t
             JOIN questao_tag qt ON qt.questao_id = t.questao_id
             JOIN questao q ON q.id = t.questao_id
            WHERE t.resultado IN ('acerto','erro') AND q.estado = 'ativa'
         GROUP BY qt.tag_id""")}

    def _sit(r):
        return situacao(r["nivel"], r["incerteza"], r["peso_total"], r["peso_bruto"])

    itens = []
    for tid, r in dom.items():
        if tid not in com_filhos or tid in excluidas:
            continue
        sit = _sit(r)
        if sit == "sem_dado":
            continue

        folhas = []
        for f in descendentes.get(tid, ()):
            fr = dom.get(f)
            # Folha de verdade: nó sem filhos. Um nó intermediário entraria
            # duas vezes na conta — como item da lista e como "folha" do pai.
            if not fr or f in com_filhos or f in excluidas:
                continue
            fsit = _sit(fr)
            if fsit == "sem_dado":
                continue
            folhas.append({
                "tag_id": f, "nome": fr["nome_canonico"],
                "nivel": round(fr["nivel"], 3), "peso": round(fr["peso_total"], 2),
                "situacao": fsit,
                "acertos": cru.get(f, (0, 0))[0], "tentativas": cru.get(f, (0, 0))[1],
            })
        if len(folhas) < config.MIN_FOLHAS_PARA_PAI:
            continue
        folhas.sort(key=lambda x: x["nivel"])

        fracas = sum(1 for f in folhas if f["situacao"] == "fraca")
        # Mesma fórmula de fraquezas(): nível baixo só sobe na fila quando há
        # peso que o sustente. O fator extra é o que diferencia um ramo de uma
        # folha solta — quantas das folhas medidas caem juntas.
        confiabilidade = r["peso_total"] / (r["peso_total"] + config.PESO_MEDIDA)
        prioridade = ((1 - r["nivel"]) * confiabilidade
                      * (1 + config.PESO_FOLHAS_FRACAS * fracas / len(folhas)))

        causa = []
        if fracas >= 2:
            causa.append(f"{fracas} das {len(folhas)} folhas medidas estão fracas: "
                         f"a falha provável é no conceito-pai, não em cada folha")
        elif fracas == 1:
            causa.append(f"1 folha fraca de {len(folhas)} medidas: o buraco parece "
                         f"pontual, não do ramo inteiro")
        else:
            causa.append(f"nenhuma folha isolada é fraca, mas as {len(folhas)} "
                         f"medidas ficam abaixo do domínio juntas")
        sem_dado = sum(1 for f in descendentes.get(tid, ())
                       if f not in com_filhos and f not in excluidas
                       and (f not in dom or _sit(dom[f]) == "sem_dado"))
        if sem_dado:
            causa.append(f"{sem_dado} folha(s) do ramo ainda sem dado: o número do "
                         f"pai fala só das {len(folhas)} medidas")
        if r["transversal"]:
            causa.append("ramo transversal: estas folhas também vivem nos ramos "
                         "temáticos, então isto não é cobertura de conteúdo novo — "
                         "é o quanto você separa os pares que confunde")

        itens.append({
            "tag_id": tid,
            "nome": r["nome_canonico"],
            "caminho": [nomes.get(p, p) for p in sorted(pais_diretos.get(tid, ()))],
            "nivel": round(r["nivel"], 3),
            "incerteza": round(r["incerteza"], 3),
            "peso": round(r["peso_total"], 2),
            "situacao": sit,
            "medida": r["peso_total"] >= config.PESO_MEDIDA,
            "transversal": bool(r["transversal"]),
            "prioridade": round(prioridade, 4),
            "folhas_fracas": fracas,
            "folhas_medidas": len(folhas),
            "folhas_sem_dado": sem_dado,
            "acertos": cru.get(tid, (0, 0))[0],
            "tentativas": cru.get(tid, (0, 0))[1],
            "justificativa": causa,
            "folhas": folhas,
        })

    itens.sort(key=lambda x: -x["prioridade"])
    return itens[:limite]
