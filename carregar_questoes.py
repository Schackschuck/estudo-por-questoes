"""
Carrega os bancos de questões de `questoes/*.json` para dentro do estudo.db.

    python carregar_questoes.py            # carrega e relata
    python carregar_questoes.py --so-checar # só audita, não grava

Formato de cada item (chaves curtas, o arquivo é grande):

    {"t": "bio.colenquima",                  # tag principal (obrigatória)
     "e": "Enunciado da questão?",
     "a": ["a CORRETA vem sempre primeiro", "distrator", "distrator", "distrator"],
     "s": ["bio.esclerenquima"],             # secundárias, 0 a 2 (opcional)
     "tc": ["bio.esclerenquima", null, null]} # v0.3: conceito concorrente de
                                              # cada DISTRATOR, paralelo a a[1:]

`tc` responde "qual conceito faria esta alternativa ser a certa?" — não "que
palavra o texto usa". null quando o distrator é só uma afirmação falsa, sem
conceito por trás: etiqueta decorativa vira ruído (seção 6.1).

A correta vem primeiro no arquivo só para facilitar a revisão humana. Na hora
de gravar, ela é **rotacionada** pela posição do item, o que dá distribuição
exatamente uniforme entre A, B, C e D — nenhum padrão de letra para o aluno ler.

Antes de gravar, o script audita a pista formal (seção 6.3): se a alternativa
correta for sistematicamente mais longa que os distratores, a questão entrega
a resposta pela forma e o banco mede leitura de prova, não domínio.
"""
import glob
import json
import os
import statistics
import sys

import config
import db
import importador
import modelo
import taxonomia

RAIZ = os.path.dirname(os.path.abspath(__file__))


def carregar_arquivos():
    itens = []
    for caminho in sorted(glob.glob(os.path.join(RAIZ, "questoes", "*.json"))):
        with open(caminho, encoding="utf-8") as f:
            lote = json.load(f)
        for i, q in enumerate(lote):
            q["_origem"] = f"{os.path.basename(caminho)}#{i}"
        itens += lote
    return itens


def montar(q, posicao):
    """Rotaciona a correta para a posição `posicao % n` e devolve as alternativas.

    O conceito concorrente viaja junto com o texto do distrator: a rotação não
    pode desalinhar os dois. A alternativa correta nunca tem concorrente — ela
    é a certa, não há com o que confundir."""
    alts = list(q["a"])
    tcs = list(q.get("tc") or [])
    correta = alts.pop(0)
    tcs += [None] * (len(alts) - len(tcs))
    pares = list(zip(alts, tcs))
    destino = posicao % (len(pares) + 1)
    pares.insert(destino, (correta, None))
    letras = [chr(65 + i) for i in range(len(pares))]
    return ([{"letra": l, "texto": t, "tc": c}
             for l, (t, c) in zip(letras, pares)], letras[destino])


def auditar(itens, tags_validas):
    """Devolve (problemas, estatísticas). Problema aqui é motivo para não gravar."""
    problemas, mais_longa, razoes, por_letra = [], 0, [], {}
    distratores = com_concorrente = 0
    vistos = set()
    for i, q in enumerate(itens):
        o = q["_origem"]
        todas = [q["t"]] + list(q.get("s") or [])
        for t in todas:
            if t not in tags_validas:
                problemas.append(f"{o}: tag inexistente no YAML: {t}")
        tc = list(q.get("tc") or [])
        if len(tc) > len(q["a"]) - 1:
            problemas.append(f"{o}: 'tc' tem mais entradas que distratores")
        for t in tc:
            if t is not None and t not in tags_validas:
                problemas.append(f"{o}: tag concorrente inexistente: {t}")
        if q["t"] in tc:
            problemas.append(f"{o}: um distrator aponta para a própria tag principal")
        if len(set(todas)) != len(todas):
            problemas.append(f"{o}: tag repetida na mesma questão")
        if len(todas) > config.MAX_TAGS_POR_QUESTAO:
            problemas.append(f"{o}: {len(todas)} tags (máximo {config.MAX_TAGS_POR_QUESTAO})")
        if len(q["a"]) < 4:
            problemas.append(f"{o}: só {len(q['a'])} alternativas")
        if len(set(q["a"])) != len(q["a"]):
            problemas.append(f"{o}: alternativas repetidas")
        chave = q["e"].strip().lower()
        if chave in vistos:
            problemas.append(f"{o}: enunciado duplicado")
        vistos.add(chave)

        alts, gab = montar(q, i)
        if importador.detectar_pista_formal(alts, gab):
            problemas.append(f"{o}: PISTA FORMAL — a correta é muito mais longa")
        por_letra[gab] = por_letra.get(gab, 0) + 1
        distratores += len(q["a"]) - 1
        com_concorrente += sum(1 for x in tc if x)

        comp = [len(a["texto"]) for a in alts]
        c = len(q["a"][0])
        if c == max(comp) and comp.count(max(comp)) == 1:
            mais_longa += 1
        outros = sorted(len(x) for x in q["a"][1:])
        razoes.append(c / max(statistics.median(outros), 1))

    return problemas, {
        "n": len(itens),
        "correta_e_a_mais_longa": mais_longa,
        "pct_mais_longa": 100 * mais_longa / max(len(itens), 1),
        "razao_mediana": statistics.median(razoes) if razoes else 0,
        "razao_maxima": max(razoes) if razoes else 0,
        "por_letra": por_letra,
        "distratores": distratores,
        "com_concorrente": com_concorrente,
    }


def gravar(con, itens):
    for i, q in enumerate(itens):
        alts, gab = montar(q, i)
        qid = con.execute(
            """INSERT INTO questao(enunciado,gabarito,explicacao,enunciado_negativo,
                                   pista_formal,procedencia,estado,criada_em)
               VALUES(?,?,?,0,?,?,'ativa',?)""",
            (q["e"], gab, q.get("x"),
             1 if importador.detectar_pista_formal(alts, gab) else 0,
             q.get("p", "banco gerado v1"), db.agora())).lastrowid
        for a in alts:
            con.execute(
                """INSERT INTO alternativa(questao_id,letra,texto,correta,
                                           tag_concorrente_id)
                   VALUES(?,?,?,?,?)""",
                (qid, a["letra"], a["texto"], 1 if a["letra"] == gab else 0,
                 a["tc"] if a["letra"] != gab else None))
        con.execute("INSERT INTO questao_tag VALUES(?,?,'principal',?)",
                    (qid, q["t"], config.PESO_PRINCIPAL))
        for s in (q.get("s") or []):
            con.execute("INSERT OR IGNORE INTO questao_tag VALUES(?,?,'secundaria',?)",
                        (qid, s, config.PESO_SECUNDARIA))
    con.commit()


def atualizar_concorrentes(con, itens):
    """
    Preenche `tag_concorrente_id` nas questões JÁ gravadas, sem recriar nada.

    Existe para não destruir histórico: o banco pode ter tentativas e eventos
    seus, e etiquetar distrator é acrescentar informação a uma coluna que
    nasceu vazia (seção 6.2) — nunca motivo para recarregar do zero.

    Casa pelo texto do enunciado e da alternativa: a rotação das letras muda
    a posição, nunca o texto.
    """
    achadas = faltando = escritas = 0
    for q in itens:
        r = con.execute("SELECT id FROM questao WHERE enunciado=?", (q["e"],)).fetchone()
        if not r:
            faltando += 1
            continue
        achadas += 1
        for texto, tc in zip(q["a"][1:], (q.get("tc") or [])):
            if not tc:
                continue
            escritas += con.execute(
                """UPDATE alternativa SET tag_concorrente_id=?
                    WHERE questao_id=? AND texto=? AND correta=0""",
                (tc, r["id"], texto)).rowcount
    con.commit()
    return achadas, faltando, escritas


def cobertura(con, itens, arv):
    folhas = {t for t in arv["folhas"] if arv["atributos"][t].get("diagnostica", True)}
    peso = {t: 0.0 for t in folhas}
    for q in itens:
        if q["t"] in peso:
            peso[q["t"]] += config.PESO_PRINCIPAL
        for s in (q.get("s") or []):
            if s in peso:
                peso[s] += config.PESO_SECUNDARIA
    return {
        "folhas": len(folhas),
        "sem_questao": sorted(t for t in folhas if peso[t] == 0),
        "cinzas": sorted(t for t in folhas if peso[t] < config.PESO_MINIMO_SINAL),
        "medidas": sum(1 for t in folhas if peso[t] >= config.PESO_MEDIDA),
    }


def main():
    so_checar = "--so-checar" in sys.argv
    con = db.conectar()
    db.criar_schema(con)
    arv = taxonomia.rebuild(con)
    tags_validas = {r["id"] for r in con.execute("SELECT id FROM tag")}

    itens = carregar_arquivos()
    if not itens:
        print("Nenhum arquivo em questoes/*.json"); return
    problemas, st = auditar(itens, tags_validas)

    print(f"\n{st['n']} questões em questoes/*.json")
    print(f"  correta é a mais longa: {st['correta_e_a_mais_longa']} "
          f"({st['pct_mais_longa']:.0f}% — o acaso daria 25%)")
    print(f"  razão de comprimento correta/mediana dos distratores: "
          f"mediana {st['razao_mediana']:.2f}, máxima {st['razao_maxima']:.2f} "
          f"(a flag dispara em {config.PISTA_FORMAL_RAZAO})")
    print(f"  distratores com conceito concorrente: {st['com_concorrente']} "
          f"de {st['distratores']} ({100*st['com_concorrente']/max(st['distratores'],1):.0f}%)")
    print(f"  gabaritos por letra: " +
          "  ".join(f"{k}:{v}" for k, v in sorted(st["por_letra"].items())))

    cob = cobertura(con, itens, arv)
    print(f"  folhas diagnósticas: {cob['folhas']} | sem nenhuma questão: "
          f"{len(cob['sem_questao'])} | ainda cinzas (peso<1): {len(cob['cinzas'])}")
    if cob["sem_questao"]:
        print("    " + ", ".join(cob["sem_questao"][:12]))

    if problemas:
        print(f"\n{len(problemas)} problema(s) — nada foi gravado:")
        for p in problemas[:25]:
            print("  -", p)
        sys.exit(1)
    if so_checar:
        print("\nSó checagem. Nada gravado.")
        return

    ja = con.execute("SELECT COUNT(*) n FROM questao").fetchone()["n"]
    if ja:
        # Banco ja povoado: acrescenta o que faltava, nunca recria. Suas
        # tentativas e eventos sao intocaveis.
        achadas, faltando, escritas = atualizar_concorrentes(con, itens)
        modelo.calcular(con)
        tent = con.execute("SELECT COUNT(*) n FROM tentativa").fetchone()["n"]
        print()
        print(f"{ja} questoes ja estavam no banco - nada foi recriado.")
        print(f"  conceitos concorrentes gravados: {escritas}")
        print(f"  questoes casadas pelo enunciado: {achadas}"
              + (f" (nao encontradas: {faltando})" if faltando else ""))
        print(f"  suas tentativas continuam la: {tent}")
        return
    gravar(con, itens)
    modelo.calcular(con)
    print(f"\n{len(itens)} questões gravadas em {config.ARQUIVO_BANCO}.")


if __name__ == "__main__":
    main()
