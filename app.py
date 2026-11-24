"""
App web local, mobile-first (seção 8). Python puro + SQLite + HTML servido
direto: sem build, sem framework de front, sem autenticação, sem deploy.

    python app.py

Abre no navegador e fica acessível pelo celular na mesma rede.
"""
import json
import os
import random
import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config
import db
import importador
import llm
import modelo
import taxonomia

RAIZ = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(RAIZ, "web")
rotas = []


def rota(metodo, padrao):
    def dec(f):
        rotas.append((metodo, re.compile(f"^{padrao}$"), f))
        return f
    return dec


# --------------------------------------------------------------- taxonomia

@rota("GET", "/api/arvore")
def api_arvore(h, m, q, corpo):
    """Árvore inteira com domínio por tag, para o painel e para etiquetar."""
    con = h.con
    tags = {r["id"]: dict(r) for r in con.execute(
        "SELECT id, nome_canonico, definicao, diagnostica FROM tag WHERE ausente_do_arquivo=0")}
    for r in con.execute("SELECT * FROM dominio_tag"):
        if r["tag_id"] in tags:
            t = tags[r["tag_id"]]
            t.update(nivel=r["nivel"], incerteza=r["incerteza"], peso=r["peso_total"],
                     peso_bruto=r["peso_bruto"],
                     situacao=modelo.situacao(r["nivel"], r["incerteza"],
                                              r["peso_total"], r["peso_bruto"]))
    for t in tags.values():
        t.setdefault("nivel", None)
        t.setdefault("peso", 0.0)
        t.setdefault("peso_bruto", 0.0)
        t.setdefault("situacao", "sem_dado")
        t["questoes"] = 0
    for r in con.execute("SELECT tag_id, COUNT(*) n FROM questao_tag GROUP BY tag_id"):
        if r["tag_id"] in tags:
            tags[r["tag_id"]]["questoes"] = r["n"]
    for t in tags.values():
        t["acertos"], t["tentativas"] = 0, 0
    for r in con.execute(
            """SELECT qt.tag_id, SUM(t.resultado='acerto') acertos, COUNT(*) n
                 FROM tentativa t
                 JOIN questao_tag qt ON qt.questao_id = t.questao_id
                 JOIN questao q ON q.id = t.questao_id
                WHERE t.resultado IN ('acerto','erro') AND q.estado = 'ativa'
             GROUP BY qt.tag_id"""):
        if r["tag_id"] in tags:
            tags[r["tag_id"]].update(acertos=r["acertos"] or 0, tentativas=r["n"])
    for r in con.execute("SELECT tag_id FROM escopo_tag"):
        if r["tag_id"] in tags:
            tags[r["tag_id"]]["excluida"] = True

    filhos, com_pai = {}, set()
    for r in con.execute("SELECT tag_id, ancestral_id FROM tag_ancestral WHERE distancia=1"):
        if r["tag_id"] in tags and r["ancestral_id"] in tags:
            filhos.setdefault(r["ancestral_id"], []).append(r["tag_id"])
            com_pai.add(r["tag_id"])
    raizes = [t for t in tags if t not in com_pai]
    return {"tags": tags, "filhos": filhos, "raizes": sorted(raizes),
            "hash": db.ler_meta(con, "taxonomia_hash")}


@rota("POST", "/api/rebuild")
def api_rebuild(h, m, q, corpo):
    """Reconstrói tag_ancestral a partir do YAML. Nenhuma tentativa é tocada."""
    try:
        r = taxonomia.rebuild(h.con)
    except taxonomia.ErroTaxonomia as e:
        return {"ok": False, "erro": str(e)}
    modelo.calcular(h.con)
    return {"ok": True, "tags": r["total_tags"], "folhas": len(r["folhas"]),
            "multipais": len(r["multipais"]), "avisos": r["avisos"],
            "nao_arquivadas": r["orfas"], "hash": r["hash"]}


# --------------------------------------------------------------- importação

@rota("POST", "/api/importar/analisar")
def api_analisar(h, m, q, corpo):
    return {"questoes": importador.separar(corpo.get("texto", ""))}


@rota("POST", "/api/importar/sugerir")
def api_sugerir(h, m, q, corpo):
    cands = [dict(r) for r in h.con.execute(
        """SELECT t.id, t.nome_canonico AS nome, t.definicao FROM tag t
            WHERE t.ausente_do_arquivo=0 AND t.diagnostica=1
              AND t.id NOT IN (SELECT ancestral_id FROM tag_ancestral)""")]
    return llm.sugerir_tags(h.con, corpo.get("questao", {}), cands)


@rota("POST", "/api/importar/salvar")
def api_salvar(h, m, q, corpo):
    con, salvas = h.con, []
    for qd in corpo.get("questoes", []):
        tags = qd.get("tags") or []
        if not qd.get("enunciado") or not qd.get("gabarito") or not tags:
            continue
        tags = tags[:config.MAX_TAGS_POR_QUESTAO]
        cur = con.execute(
            """INSERT INTO questao(enunciado,gabarito,explicacao,enunciado_negativo,
                                   pista_formal,procedencia,estado,criada_em)
               VALUES(?,?,?,?,?,?,'ativa',?)""",
            (qd["enunciado"].strip(), qd["gabarito"].upper(), qd.get("explicacao"),
             1 if qd.get("enunciado_negativo") else 0,
             1 if qd.get("pista_formal") else 0, qd.get("procedencia"), db.agora()))
        qid = cur.lastrowid
        for a in qd.get("alternativas", []):
            con.execute(
                "INSERT INTO alternativa(questao_id,letra,texto,correta) VALUES(?,?,?,?)",
                (qid, a["letra"].upper(), a["texto"],
                 1 if a["letra"].upper() == qd["gabarito"].upper() else 0))
        for i, t in enumerate(tags):
            papel = "principal" if i == 0 else "secundaria"
            peso = config.PESO_PRINCIPAL if i == 0 else config.PESO_SECUNDARIA
            con.execute("INSERT OR IGNORE INTO questao_tag(questao_id,tag_id,papel,peso)"
                        " VALUES(?,?,?,?)", (qid, t, papel, peso))
        salvas.append(qid)
    con.commit()
    return {"ok": True, "salvas": len(salvas), "ids": salvas,
            "coladas": modelo.tags_coladas(con)}


# ---------------------------------------------------------------- resolução

@rota("POST", "/api/sessao")
def api_sessao(h, m, q, corpo):
    modo = corpo.get("modo", "estudo")
    alvo = corpo.get("tags_alvo") or None
    cur = h.con.execute("INSERT INTO sessao(modo,inicio,tags_alvo) VALUES(?,?,?)",
                        (modo, db.agora(), json.dumps(alvo) if alvo else None))
    h.con.commit()
    return {"sessao_id": cur.lastrowid, "modo": modo,
            "eliminacao_obrigatoria": config.ELIMINACAO_OBRIGATORIA.get(modo, False),
            "pede_confianca": modo == "triagem", "instrumentada": modo != "simulado"}


def _nivel2(con):
    """
    Para cada tag, o ancestral de nível 2 (a subárea: Meristemas, Flor,
    Fotossíntese...). É a unidade de varredura da triagem: a seção 10 manda
    percorrer o nível 2 em largura, não mergulhar num ramo só.

    Tag com mais de um pai (o ramo transversal) resolve pelo ancestral mais
    próximo, com desempate alfabético — precisa ser determinístico, senão a
    varredura em largura vira sorteio.
    """
    anc = {}
    for r in con.execute("SELECT tag_id, ancestral_id, distancia FROM tag_ancestral"):
        anc.setdefault(r["tag_id"], []).append((r["distancia"], r["ancestral_id"]))
    raizes = {t for t in anc if not anc.get(t)} | {
        r["id"] for r in con.execute("SELECT id FROM tag")
        if r["id"] not in anc}
    nivel2 = {t for t, pais in anc.items()
              if len(pais) == 1 and pais[0][1] in raizes}
    # Subárea do ramo transversal não conta como cobertura do programa: a folha
    # já vive num ramo temático, e é lá que a varredura precisa marcar a visita.
    transv = {r["id"] for r in con.execute("SELECT id FROM tag WHERE transversal=1")}
    sob_transversal = {t for t in nivel2
                       if any(x in transv for _, x in anc.get(t, ()))}
    saida = {}
    for t in anc:
        if t in nivel2:
            saida[t] = t
            continue
        candidatos = sorted((x in sob_transversal, d, x)
                            for d, x in anc[t] if x in nivel2)
        if candidatos:
            saida[t] = candidatos[0][2]
    for t in nivel2:
        saida[t] = t
    return saida


def _escopo_excluido(con):
    """'Não preciso desse conteúdo' exclui o ramo inteiro abaixo da tag."""
    fora = {r["tag_id"] for r in con.execute("SELECT tag_id FROM escopo_tag")}
    if not fora or not config.EXCLUIR_RAMO_INTEIRO:
        return fora
    todos = set(fora)
    for r in con.execute("SELECT tag_id, ancestral_id FROM tag_ancestral"):
        if r["ancestral_id"] in fora:
            todos.add(r["tag_id"])
    return todos


@rota("GET", "/api/proxima")
def api_proxima(h, m, q, corpo):
    """
    Serve a próxima questão. Prioriza tag fraca e questão pouco exposta;
    nunca repete questão já respondida nesta sessão.
    """
    con = h.con
    sessao = q.get("sessao", [None])[0]
    tag = q.get("tag", [None])[0]
    evitar = {int(x) for x in q.get("evitar", [""])[0].split(",") if x.strip().isdigit()}

    modo = "estudo"
    if sessao:
        r = con.execute("SELECT modo FROM sessao WHERE id=?", (sessao,)).fetchone()
        if r:
            modo = r["modo"]

    # Triagem resolve o INÍCIO FRIO: o que interessa é assunto sem nenhum dado,
    # não assunto fraco. Tag fraca já tem diagnóstico; tag virgem não tem nada,
    # e medir o que não foi medido rende muito mais por questão.
    triagem = modo == "triagem"
    bruto, nivel2, visitas = {}, {}, {}
    if triagem:
        bruto = {r["tag_id"]: r["peso_bruto"]
                 for r in con.execute("SELECT tag_id, peso_bruto FROM dominio_tag")}
        nivel2 = _nivel2(con)
        # quantas já respondi de cada subárea NESTA sessão, para varrer em
        # largura em vez de afundar num ramo só
        for r in con.execute(
                """SELECT qt.tag_id FROM tentativa t
                     JOIN questao_tag qt ON qt.questao_id = t.questao_id
                                        AND qt.papel = 'principal'
                    WHERE t.sessao_id = ?""", (sessao,)):
            ramo = nivel2.get(r["tag_id"])
            if ramo:
                visitas[ramo] = visitas.get(ramo, 0) + 1

    fora = _escopo_excluido(con)
    cand = []
    consulta = ("SELECT id, exposicoes FROM questao WHERE estado='ativa'"
                + (" AND serve_para_triagem=1" if triagem else ""))
    for r in con.execute(consulta):
        tags = [x["tag_id"] for x in con.execute(
            "SELECT tag_id FROM questao_tag WHERE questao_id=?", (r["id"],))]
        if not tags or any(t in fora for t in tags):
            continue
        if tag:
            ok = tag in tags or any(x["ancestral_id"] == tag for x in con.execute(
                "SELECT ancestral_id FROM tag_ancestral WHERE tag_id IN (%s)"
                % ",".join("?" * len(tags)), tags))
            if not ok:
                continue
        if r["id"] in evitar:
            continue
        if sessao and con.execute(
                "SELECT 1 FROM tentativa WHERE questao_id=? AND sessao_id=?",
                (r["id"], sessao)).fetchone():
            continue
        if triagem:
            principal = next((x["tag_id"] for x in con.execute(
                "SELECT tag_id FROM questao_tag WHERE questao_id=? AND papel='principal'",
                (r["id"],))), tags[0])
            virgem = bruto.get(principal, 0.0) < config.PESO_MINIMO_SINAL
            ramo = nivel2.get(principal, "")
            # ordena: virgem primeiro, depois a subárea menos visitada na
            # sessão, depois questão menos exposta, sorteio no fim
            cand.append(((0 if virgem else 1, visitas.get(ramo, 0),
                          r["exposicoes"] or 0, random.random()), r["id"]))
        else:
            d = con.execute(
                """SELECT MIN(COALESCE(d.nivel, 0.5)) n FROM dominio_tag d
                    WHERE d.tag_id IN (%s)""" % ",".join("?" * len(tags)), tags).fetchone()
            nivel = d["n"] if d and d["n"] is not None else 0.5
            # quanto mais fraca a tag e menos exposta a questão, maior a chance
            peso = (1.3 - nivel) / (1 + (r["exposicoes"] or 0))
            cand.append((-peso, r["id"]))
    if not cand:
        return {"fim": True}

    cand.sort(key=lambda x: x[0])
    if triagem:
        qid = cand[0][1]
    else:
        topo = cand[:max(3, len(cand) // 4)]
        qid = random.choice(topo)[1]

    saida = _questao(con, qid)
    if triagem:
        principal = next((t["id"] for t in saida["tags"] if t["papel"] == "principal"),
                         None)
        saida["assunto_novo"] = bruto.get(principal, 0.0) < config.PESO_MINIMO_SINAL
        saida["subarea"] = nivel2.get(principal)
    return saida


def _questao(con, qid):
    r = con.execute("SELECT * FROM questao WHERE id=?", (qid,)).fetchone()
    alts = [{"id": a["id"], "letra": a["letra"], "texto": a["texto"]}
            for a in con.execute(
                "SELECT * FROM alternativa WHERE questao_id=? ORDER BY letra", (qid,))]
    tags = [{"id": t["tag_id"], "nome": t["nome_canonico"], "papel": t["papel"]}
            for t in con.execute(
                """SELECT qt.tag_id, qt.papel, tg.nome_canonico FROM questao_tag qt
                     JOIN tag tg ON tg.id = qt.tag_id WHERE qt.questao_id=?""", (qid,))]
    exp = con.execute(
        "SELECT COUNT(*) n FROM tentativa WHERE questao_id=? AND resultado<>'descartada'",
        (qid,)).fetchone()["n"]
    return {"id": qid, "enunciado": r["enunciado"], "alternativas": alts,
            "tags": tags, "procedencia": r["procedencia"],
            "enunciado_negativo": bool(r["enunciado_negativo"]),
            "exposicoes_anteriores": exp}


@rota("POST", "/api/tentativa")
def api_tentativa(h, m, q, corpo):
    """
    Grava a tentativa e devolve a correção. É aqui que o processo vira dado:
    eliminações com timestamp, trocas, tempos, confiança e meta-resposta.
    """
    con = h.con
    qid = int(corpo["questao_id"])
    meta = corpo.get("meta_resposta")
    escolhida = (corpo.get("alternativa") or "").upper() or None
    eventos = corpo.get("eventos") or []
    # saldo líquido: riscar e depois desmarcar não conta como eliminação
    saldo = {}
    for e in eventos:
        aid = e.get("alternativa_id")
        if aid is None:
            continue
        saldo[aid] = saldo.get(aid, 0) + {"eliminou": 1,
                                          "desmarcou_eliminacao": -1}.get(e.get("tipo"), 0)
    riscadas = {aid for aid, v in saldo.items() if v > 0}
    n_elim = len(riscadas)
    riscou_a_correta = any(
        r["id"] in riscadas and r["correta"]
        for r in con.execute("SELECT id, correta FROM alternativa WHERE questao_id=?", (qid,)))

    gab = con.execute("SELECT gabarito FROM questao WHERE id=?", (qid,)).fetchone()["gabarito"]
    exp = con.execute(
        "SELECT COUNT(*) n FROM tentativa WHERE questao_id=? AND resultado<>'descartada'",
        (qid,)).fetchone()["n"]

    if meta == "ja_fiz":
        resultado = "descartada"          # não conta como acerto nem erro
    elif meta == "nao_faco_ideia":
        resultado = "erro"                # lacuna declarada: erro de outro tipo
    elif meta == "nao_preciso":
        resultado = "descartada"
    else:
        resultado = "acerto" if escolhida == gab else "erro"

    cls = {"tipo_de_erro": None, "suspeita_chute": 0, "dominio_ilusorio": 0}
    if resultado != "descartada":
        cls = modelo.classificar(con, qid, resultado, meta, corpo.get("confianca"),
                                 corpo.get("tempo_total"), n_elim, riscou_a_correta)

    peso = modelo._peso_exposicao(exp) if resultado != "descartada" else 0.0
    cur = con.execute(
        """INSERT INTO tentativa(questao_id,sessao_id,alternativa_escolhida,meta_resposta,
              resultado,tipo_de_erro,confianca_declarada,tempo_total,
              tempo_ate_primeira_eliminacao,exposicoes_anteriores,peso,
              suspeita_chute,dominio_ilusorio,criada_em)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (qid, corpo.get("sessao_id"), escolhida, meta, resultado, cls["tipo_de_erro"],
         corpo.get("confianca"), corpo.get("tempo_total"),
         corpo.get("tempo_primeira_eliminacao"), exp, peso,
         cls["suspeita_chute"], cls["dominio_ilusorio"], db.agora()))
    tid = cur.lastrowid
    for e in eventos:
        con.execute("INSERT INTO evento(tentativa_id,tipo,alternativa_id,timestamp_relativo)"
                    " VALUES(?,?,?,?)",
                    (tid, e.get("tipo"), e.get("alternativa_id"), e.get("t", 0)))

    if meta == "nao_preciso":
        for t in con.execute("SELECT tag_id FROM questao_tag WHERE questao_id=? AND papel='principal'",
                             (qid,)):
            con.execute("INSERT OR IGNORE INTO escopo_tag(tag_id,excluida_em) VALUES(?,?)",
                        (t["tag_id"], db.agora()))
    if resultado != "descartada":
        con.execute("UPDATE questao SET exposicoes = exposicoes + 1 WHERE id=?", (qid,))
    con.commit()

    # Quanto do raciocínio ficou explícito nesta tentativa (seção 9).
    d = modelo.discriminacao(con, tid).get(tid) or {}
    fracao = d.get("fracao", 0.0)
    quarentena = modelo.checar_quarentena(con, qid)
    modelo.calcular(con)

    r = con.execute("SELECT explicacao FROM questao WHERE id=?", (qid,)).fetchone()
    return {"resultado": resultado, "gabarito": gab, "tentativa_id": tid,
            "tipo_de_erro": cls["tipo_de_erro"], "suspeita_chute": bool(cls["suspeita_chute"]),
            "dominio_ilusorio": bool(cls["dominio_ilusorio"]),
            "explicacao": r["explicacao"], "quarentena": quarentena,
            "discriminacao": round(fracao, 3),
            "riscou_a_correta": riscou_a_correta,
            "peso_multiplicador": round(modelo.multiplicador(
                fracao, riscou_a_correta, resultado == "erro"), 2),
            "credito_parcial": round(config.CREDITO_ELIMINACAO * fracao, 3),
            "descartada": resultado == "descartada"}


# ------------------------------------------------------------------ painel

@rota("GET", "/api/painel")
def api_painel(h, m, q, corpo):
    con = h.con
    tot = con.execute(
        """SELECT (SELECT COUNT(*) FROM questao WHERE estado='ativa') ativas,
                  (SELECT COUNT(*) FROM questao WHERE estado='quarentena') quarentena,
                  (SELECT COUNT(*) FROM tentativa WHERE resultado<>'descartada') tentativas,
                  (SELECT COUNT(*) FROM tentativa WHERE resultado='descartada') descartadas,
                  (SELECT COUNT(*) FROM sessao) sessoes""").fetchone()
    return {"totais": dict(tot),
            "hoje": modelo.fraquezas(con),
            "pais": modelo.fraquezas_pai(con),
            "coladas": modelo.tags_coladas(con),
            "confusao": modelo.confusao(con),
            "calibracao_secundaria": modelo.calibrar_peso_secundaria(con),
            "llm_chamadas": int(db.ler_meta(con, "llm_chamadas", "0")),
            "llm_ligado": llm.disponivel(),
            "hash": db.ler_meta(con, "taxonomia_hash")}


@rota("POST", "/api/escopo")
def api_escopo(h, m, q, corpo):
    tid = corpo["tag_id"]
    if corpo.get("excluir"):
        h.con.execute("INSERT OR IGNORE INTO escopo_tag(tag_id,excluida_em) VALUES(?,?)",
                      (tid, db.agora()))
    else:
        h.con.execute("DELETE FROM escopo_tag WHERE tag_id=?", (tid,))
    h.con.commit()
    return {"ok": True}


@rota("GET", "/api/relatorio")
def api_relatorio(h, m, q, corpo):
    """Único uso legítimo de LLM no diagnóstico — ocasional, sob demanda."""
    con = h.con
    dados = {"fraquezas": modelo.fraquezas(con), "tags_coladas": modelo.tags_coladas(con),
             "hash_da_arvore": db.ler_meta(con, "taxonomia_hash")}
    return llm.relatorio(con, dados)


# ---------------------------------------------------------------- curadoria

@rota("GET", "/api/curadoria")
def api_curadoria(h, m, q, corpo):
    con = h.con
    saida = []
    for r in con.execute("SELECT * FROM questao WHERE estado<>'ativa' ORDER BY id DESC"):
        d = _questao(con, r["id"])
        d.update(estado=r["estado"], motivo=r["motivo_quarentena"], gabarito=r["gabarito"],
                 explicacao=r["explicacao"])
        stat = con.execute(
            """SELECT COUNT(*) n, SUM(resultado='erro') erros FROM tentativa
                WHERE questao_id=? AND resultado<>'descartada'""", (r["id"],)).fetchone()
        d.update(tentativas=stat["n"], erros=stat["erros"] or 0)
        saida.append(d)
    return {"questoes": saida}


@rota("POST", "/api/curadoria/(\\d+)")
def api_curar(h, m, q, corpo):
    """Curadoria manual: corrigir permanentemente, reativar ou aposentar."""
    con, qid = h.con, int(m.group(1))
    campos = {k: corpo[k] for k in ("enunciado", "gabarito", "explicacao") if k in corpo}
    if campos:
        con.execute("UPDATE questao SET " + ",".join(f"{k}=?" for k in campos) + " WHERE id=?",
                    (*campos.values(), qid))
        if "gabarito" in campos:
            con.execute("UPDATE alternativa SET correta=(letra=?) WHERE questao_id=?",
                        (campos["gabarito"].upper(), qid))
    if corpo.get("estado"):
        con.execute("UPDATE questao SET estado=?, motivo_quarentena=NULL WHERE id=?",
                    (corpo["estado"], qid))
    con.commit()
    modelo.calcular(con)
    return {"ok": True}


# ------------------------------------------------------------------ export

@rota("GET", "/api/exportar")
def api_exportar(h, m, q, corpo):
    """Exportação em JSON — nada do meu estudo fica preso aqui (seção 13)."""
    con = h.con
    return {
        "tags": [dict(r) for r in con.execute("SELECT * FROM tag")],
        "questoes": [dict(r) for r in con.execute("SELECT * FROM questao")],
        "alternativas": [dict(r) for r in con.execute("SELECT * FROM alternativa")],
        "questao_tag": [dict(r) for r in con.execute("SELECT * FROM questao_tag")],
        "tentativas": [dict(r) for r in con.execute("SELECT * FROM tentativa")],
        "eventos": [dict(r) for r in con.execute("SELECT * FROM evento")],
    }


# -------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    local = threading.local()

    @property
    def con(self):
        if not hasattr(self.local, "con"):
            self.local.con = db.conectar()
        return self.local.con

    def log_message(self, *a):
        pass

    def _json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _arquivo(self, caminho):
        tipos = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
        ext = os.path.splitext(caminho)[1]
        try:
            with open(caminho, "rb") as f:
                dados = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{tipos.get(ext, 'text/plain')}; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _despachar(self, metodo, corpo=None):
        u = urlparse(self.path)
        for met, padrao, fn in rotas:
            if met != metodo:
                continue
            m = padrao.match(u.path)
            if m:
                try:
                    return self._json(fn(self, m, parse_qs(u.query), corpo or {}))
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    return self._json({"erro": f"{type(e).__name__}: {e}"}, 500)
        if metodo == "GET":
            nome = "index.html" if u.path == "/" else u.path.lstrip("/")
            caminho = os.path.normpath(os.path.join(WEB, nome))
            if caminho.startswith(WEB) and os.path.isfile(caminho):
                return self._arquivo(caminho)
        self.send_error(404)

    def do_GET(self):
        self._despachar("GET")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            corpo = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"erro": "json inválido"}, 400)
        self._despachar("POST", corpo)


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    con = db.conectar()
    db.criar_schema(con)
    try:
        r = taxonomia.rebuild(con)
        print(f"Árvore: {r['total_tags']} tags ({len(r['folhas'])} folhas, "
              f"{len(r['multipais'])} com mais de um pai) — hash {r['hash']}")
        for a in r["avisos"]:
            print("  aviso:", a)
        if r["orfas"]:
            print("  não arquivadas:", ", ".join(r["orfas"][:10]))
    except taxonomia.ErroTaxonomia as e:
        print("ERRO na taxonomia, o app não sobe:", e)
        sys.exit(1)
    modelo.calcular(con)
    con.close()

    url = f"http://localhost:{config.PORTA}/"
    print(f"\n  Aqui:      {url}\n  No celular: http://{ip_local()}:{config.PORTA}/\n"
          f"  LLM: {'ligado' if llm.disponivel() else 'desligado (rotina diária não usa)'}\n"
          f"  Ctrl+C para parar.\n")
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("0.0.0.0", config.PORTA), Handler).serve_forever()


if __name__ == "__main__":
    main()
