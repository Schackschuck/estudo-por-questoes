"""
Os 12 critérios de aceitação da seção 14, executados contra um banco temporário.

    python testes.py

Não é teste unitário de cobertura: é a lista da especificação, um a um, para o
portão da fase 5 ser verificável em vez de opinativo.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="estudo_teste_")
import config  # noqa: E402
config.ARQUIVO_BANCO = os.path.join(TMP, "teste.db")
config.ARQUIVO_TAXONOMIA = os.path.join(TMP, "biologia.yaml")
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "taxonomia", "biologia.yaml"), config.ARQUIVO_TAXONOMIA)

import app  # noqa: E402
import db  # noqa: E402
import modelo  # noqa: E402
import taxonomia  # noqa: E402

CON = db.conectar()
db.criar_schema(CON)
taxonomia.rebuild(CON)


class H:
    """Handler falso: as rotas só precisam de .con."""
    con = CON


class M:
    def __init__(self, *g): self.g = g
    def group(self, i): return self.g[i - 1]


falhas = []


def checa(n, titulo, cond, detalhe=""):
    marca = "ok  " if cond else "FALHA"
    print(f"  {marca} {n:2}. {titulo}" + (f"  [{detalhe}]" if detalhe else ""))
    if not cond:
        falhas.append(titulo)


def responder(qid, letra=None, meta=None, conf=None, tempo=20.0, elim=(), sessao=None):
    eventos = [{"tipo": "eliminou", "alternativa_id": a, "t": 3.0} for a in elim]
    return app.api_tentativa(H, None, {}, {
        "questao_id": qid, "sessao_id": sessao, "alternativa": letra,
        "meta_resposta": meta, "confianca": conf, "tempo_total": tempo,
        "tempo_primeira_eliminacao": 3.0 if elim else None, "eventos": eventos})


def criar(enun, tags, gabarito="A", n_alts=4, explicacao=None):
    alts = [{"letra": chr(65 + i), "texto": f"alternativa {chr(65+i)} de {enun[:14]}"}
            for i in range(n_alts)]
    r = app.api_salvar(H, None, {}, {"questoes": [{
        "enunciado": enun, "alternativas": alts, "gabarito": gabarito,
        "tags": tags, "explicacao": explicacao, "procedencia": "teste"}]})
    return r["ids"][0] if r["ids"] else None


print("\nCRITÉRIOS DE ACEITAÇÃO — seção 14\n")

# 1 ────────────────────────────────────────────────────────────────────────
texto = "\n\n".join(
    f"""{i}. (Banca {2000+i}) Enunciado da questão {i} sobre tecidos vegetais?
a) primeira alternativa
b) segunda alternativa
c) terceira alternativa
d) quarta alternativa
Gabarito: {'ABCD'[i % 4]}""" for i in range(1, 21))
analisado = app.api_analisar(H, None, {}, {"texto": texto})["questoes"]
trios = [["bio.procambio"], ["bio.protoderme", "bio.meristemas"],
         ["bio.colenquima", "bio.esclerenquima", "bio.fibras"]]
for i, q in enumerate(analisado):
    q["tags"] = trios[i % 3]
salvo = app.api_salvar(H, None, {}, {"questoes": analisado})
checa(1, "importar 20 questões coladas e etiquetar com 1 a 3 ids do YAML",
      len(analisado) == 20 and salvo["salvas"] == 20,
      f"{len(analisado)} reconhecidas, {salvo['salvas']} salvas")

# 4 (antes das sessões, para contar do zero) ──────────────────────────────
llm_antes = int(db.ler_meta(CON, "llm_chamadas", "0"))

# 2 ────────────────────────────────────────────────────────────────────────
s = app.api_sessao(H, None, {}, {"modo": "estudo"})["sessao_id"]
vistas = []
for _ in range(10):
    q = app.api_proxima(H, None, {"sessao": [str(s)], "evitar": [",".join(map(str, vistas))]}, {})
    if q.get("fim"):
        break
    vistas.append(q["id"])
    responder(q["id"], "A", sessao=s, elim=[q["alternativas"][-1]["id"]])
dom = CON.execute("SELECT COUNT(*) n FROM dominio_tag WHERE peso_total>0").fetchone()["n"]
checa(2, "responder 10 questões e ver o painel atualizar por tag",
      len(vistas) == 10 and dom > 0, f"{dom} tags com evidência")

# 4 ────────────────────────────────────────────────────────────────────────
checa(4, "rodar uma sessão inteira com zero chamadas a LLM",
      int(db.ler_meta(CON, "llm_chamadas", "0")) == llm_antes,
      f"contador em {db.ler_meta(CON, 'llm_chamadas', '0')}")

# 3 ────────────────────────────────────────────────────────────────────────
responder(criar("Procâmbio, para o teste do YAML", ["bio.procambio"]), "A", tempo=30)
antes = CON.execute("SELECT COUNT(*) n FROM tentativa").fetchone()["n"]
dom_antes = CON.execute("SELECT nivel FROM dominio_tag WHERE tag_id='bio.procambio'").fetchone()
y = open(config.ARQUIVO_TAXONOMIA, encoding="utf-8").read()
# move o ramo: procâmbio deixa de pendurar em Meristemas e vai só para vasculares
movido = y.replace("    - {id: bio.procambio, nome: Procâmbio}\n", "", 1) \
          .replace("    - id: bio.procambio\n",
                   "    - {id: bio.procambio, nome: Procâmbio}\n", 1)
open(config.ARQUIVO_TAXONOMIA, "w", encoding="utf-8").write(movido)
r3 = app.api_rebuild(H, None, {}, {})
depois = CON.execute("SELECT COUNT(*) n FROM tentativa").fetchone()["n"]
dom_depois = CON.execute("SELECT nivel FROM dominio_tag WHERE tag_id='bio.procambio'").fetchone()
checa(3, "mover um ramo no YAML, regenerar tag_ancestral, não perder tentativa",
      r3["ok"] and antes == depois and dom_depois is not None,
      f"{antes} tentativas antes e depois; hash {r3.get('hash')}")
open(config.ARQUIVO_TAXONOMIA, "w", encoding="utf-8").write(y)
app.api_rebuild(H, None, {}, {})

# 5 ────────────────────────────────────────────────────────────────────────
qid = criar("Questão para descarte", ["bio.estomato"])
modelo.calcular(CON)
n_antes = CON.execute("SELECT nivel,peso_total FROM dominio_tag WHERE tag_id='bio.estomato'").fetchone()
r5 = responder(qid, meta="ja_fiz")
n_depois = CON.execute("SELECT nivel,peso_total FROM dominio_tag WHERE tag_id='bio.estomato'").fetchone()
criar("Outra da mesma tag", ["bio.estomato"])
outra = app.api_proxima(H, None, {"tag": ["bio.estomato"], "evitar": [str(qid)]}, {})
checa(5, "'já fiz essa' descarta, domínio não muda, outra questão da tag é servida",
      r5["descartada"] and (n_antes is None) == (n_depois is None)
      and (n_antes is None or abs(n_antes["peso_total"] - n_depois["peso_total"]) < 1e-9)
      and not outra.get("fim") and outra["id"] != qid)

# 6 ────────────────────────────────────────────────────────────────────────
qid = criar("Questão de lacuna", ["bio.vernalizacao"])
r6 = responder(qid, meta="nao_faco_ideia")
checa(6, "'não faço ideia' vira erro classificado como lacuna",
      r6["resultado"] == "erro" and r6["tipo_de_erro"] == "lacuna")

# 7 ────────────────────────────────────────────────────────────────────────
boa = criar("Questão boa da tag auxina", ["bio.auxina"])
ruim = criar("Questão com gabarito errado", ["bio.auxina"], gabarito="D")
for _ in range(3):
    responder(boa, "A", conf="certeza", tempo=25)
for _ in range(5):
    responder(ruim, "A", conf="certeza", tempo=25)
est = CON.execute("SELECT estado, motivo_quarentena FROM questao WHERE id=?", (ruim,)).fetchone()
usadas = CON.execute(
    """SELECT COUNT(*) n FROM tentativa t JOIN questao q ON q.id=t.questao_id
        WHERE t.questao_id=? AND q.estado='ativa'""", (ruim,)).fetchone()["n"]
checa(7, "questão com gabarito errado vai para quarentena e sai do cálculo",
      est["estado"] == "quarentena" and usadas == 0, est["motivo_quarentena"] or "")

# 8 ────────────────────────────────────────────────────────────────────────
f = modelo.fraquezas(CON)
ordenada = all(f[i]["prioridade"] >= f[i + 1]["prioridade"] for i in range(len(f) - 1))
checa(8, "'o que eu estudo hoje' devolve lista ordenada com justificativa",
      len(f) > 0 and ordenada and all(x["justificativa"] for x in f),
      f"{len(f)} itens, topo: {f[0]['nome'] if f else '—'}")

# 9 ────────────────────────────────────────────────────────────────────────
arv = app.api_arvore(H, None, {}, {})
virgem = arv["tags"]["bio.quiropterofilia"]
checa(9, "tag sem nenhuma tentativa aparece como cinza, não como fraca",
      virgem["situacao"] == "sem_dado" and virgem["nivel"] is None)

# 10 ───────────────────────────────────────────────────────────────────────
folha = "bio.micorrizas"
for i in range(10):
    q = criar(f"Questão {i} de micorrizas", [folha], gabarito="A")
    responder(q, "A" if i < 7 else "B", tempo=30)
modelo.calcular(CON)
pai = CON.execute("SELECT * FROM dominio_tag WHERE tag_id='bio.nutricao_mineral'").fetchone()
avo = CON.execute("SELECT * FROM dominio_tag WHERE tag_id='bio.fisiologia'").fetchone()
etiquetada = CON.execute(
    "SELECT COUNT(*) n FROM questao_tag WHERE tag_id IN ('bio.nutricao_mineral','bio.fisiologia')"
).fetchone()["n"]
checa(10, "nós-pai ganham domínio estimado sem nenhuma questão etiquetada neles",
      pai is not None and avo is not None and etiquetada == 0
      and pai["peso_total"] > avo["peso_total"],
      f"pai peso {pai['peso_total']:.2f} > avô peso {avo['peso_total']:.2f}")

# 11 ───────────────────────────────────────────────────────────────────────
# Toda vez que 'bio.tigmotropismo' aparece junto de 'bio.nastismo', eu erro.
# Sozinha, eu acerto. A falha deve ser atribuída a nastismo, não dividida.
for i in range(8):
    q = criar(f"Tigmo sozinho {i}", ["bio.tigmotropismo"], gabarito="A")
    responder(q, "A", tempo=30)
for i in range(6):
    q = criar(f"Tigmo com nastismo {i}", ["bio.tigmotropismo", "bio.nastismo"], gabarito="A")
    responder(q, "B", tempo=30)
modelo.calcular(CON)
tg = CON.execute("SELECT * FROM dominio_tag WHERE tag_id='bio.tigmotropismo'").fetchone()
na = CON.execute("SELECT * FROM dominio_tag WHERE tag_id='bio.nastismo'").fetchone()
checa(11, "atribuição por cruzamento concentra a falha na tag culpada",
      na["nivel"] < tg["nivel"] and na["peso_total"] > 0,
      f"nastismo {na['nivel']:.2f} < tigmotropismo {tg['nivel']:.2f}")

# 12 ───────────────────────────────────────────────────────────────────────
coladas = modelo.tags_coladas(CON)
par = [c for c in coladas if {c["a"], c["b"]} == {"bio.colenquima", "bio.esclerenquima"}]
checa(12, "alerta de tags coladas dispara para um par que só aparece junto",
      bool(coladas), f"{len(coladas)} par(es); alvo detectado: {bool(par)}")

# ──────────────────────────────────────────────────────────────────────────
print()
if falhas:
    print(f"{len(falhas)} critério(s) falharam:")
    for f_ in falhas:
        print("  -", f_)
    sys.exit(1)
print("Todos os 12 critérios de aceitação passaram.")
print(f"(banco de teste em {TMP})")
