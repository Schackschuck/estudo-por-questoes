"""
Carrega a arvore de tags do YAML e materializa tag_ancestral.

A arvore e arquivo versionavel, nao tabela (secao 5). Consequencias que este
modulo garante:
  - mover um ramo e editar texto: nenhuma tentativa e invalidada, porque
    tentativa guarda tag.id e nunca caminho;
  - uma tag pode pendurar em dois pais sem virar duplicata;
  - tag_ancestral e derivada e descartavel, regenerada a cada rebuild.

Formato do arquivo:

    disciplina: Biologia
    arvore:
      - id: bio.histologia_vegetal
        nome: Histologia vegetal
        filhos:
          - id: bio.meristemas
            nome: Meristemas
            filhos:
              - {id: bio.procambio, nome: Procambio}

Para pendurar a MESMA tag em outro ramo, basta repetir o id sem `nome`:

      - id: bio.tecidos_vasculares
        nome: Tecidos vasculares
        filhos:
          - id: bio.procambio        # referencia, nao duplicata

Chaves opcionais por no: definicao, sinonimos (lista), diagnostica (bool).
"""
import hashlib
import os
from collections import deque

import yaml

import config
import db


class ErroTaxonomia(Exception):
    """Erro que aborta o rebuild (secao 5: 'e erro e aborta')."""


def _hash(caminho: str) -> str:
    with open(caminho, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def _coletar(nos, pai, atributos, arestas, ordem, raizes, erros, caminho_atual):
    """Percorre o YAML acumulando atributos e arestas pai->filho."""
    if not nos:
        return
    if not isinstance(nos, list):
        erros.append(f"'filhos' de {pai or 'raiz'} deveria ser uma lista")
        return
    for no in nos:
        if isinstance(no, str):
            no = {"id": no}
        if not isinstance(no, dict) or not no.get("id"):
            erros.append(f"no sem 'id' abaixo de {pai or 'raiz'}: {no!r}")
            continue
        tid = str(no["id"]).strip()
        if tid in caminho_atual:
            erros.append(f"ciclo na arvore: {tid} e ancestral de si mesmo")
            continue
        if tid not in ordem:
            ordem.append(tid)
        attr = atributos.setdefault(tid, {})
        for chave in ("nome", "definicao", "diagnostica", "sinonimos",
                      "transversal"):
            if chave in no and chave not in attr:
                attr[chave] = no[chave]
        if pai:
            arestas.setdefault(tid, set()).add(pai)
        else:
            raizes.add(tid)
        _coletar(no.get("filhos"), tid, atributos, arestas, ordem, raizes,
                 erros, caminho_atual | {tid})


def carregar_arquivo(caminho: str) -> dict:
    """Le e valida o YAML. Levanta ErroTaxonomia se algo abortar o rebuild."""
    with open(caminho, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    if "arvore" not in doc:
        raise ErroTaxonomia("arquivo sem a chave 'arvore'")

    atributos, arestas, ordem, raizes, erros = {}, {}, [], set(), []
    _coletar(doc["arvore"], None, atributos, arestas, ordem, raizes, erros, frozenset())

    # Id que aparece so como referencia e nunca foi definido com nome: erro.
    for tid in ordem:
        if not atributos.get(tid, {}).get("nome"):
            erros.append(f"'{tid}' e referenciado mas nunca definido com 'nome'")
    if erros:
        raise ErroTaxonomia("; ".join(erros[:10]))

    # Ancestrais por BFS subindo pelas arestas (varios pais somam ancestrais,
    # e fica a MENOR distancia de cada um).
    ancestrais = {}
    for tid in ordem:
        dist = {}
        fila = deque((p, 1) for p in arestas.get(tid, ()))
        while fila:
            atual, d = fila.popleft()
            if d > config.DISTANCIA_MAXIMA_ANCESTRAL:
                continue
            if atual in dist and dist[atual] <= d:
                continue
            dist[atual] = d
            for p in arestas.get(atual, ()):
                fila.append((p, d + 1))
        ancestrais[tid] = dist

    # Profundidade: tres niveis abaixo da disciplina (raiz = nivel 1).
    avisos = []
    for tid in ordem:
        prof = 1 + (min(ancestrais[tid].values()) if ancestrais[tid] else 0)
        if prof > 3:
            avisos.append(f"{tid} esta no nivel {prof}; a regra sao tres niveis "
                          f"abaixo da disciplina (secao 5)")

    filhos_de = {}
    for filho, pais in arestas.items():
        for p in pais:
            filhos_de.setdefault(p, []).append(filho)
    folhas = [t for t in ordem if t not in filhos_de]
    multipais = [t for t, p in arestas.items() if len(p) > 1]

    return {
        "disciplina": doc.get("disciplina", os.path.basename(caminho)),
        "ordem": ordem,
        "atributos": atributos,
        "ancestrais": ancestrais,
        "raizes": [t for t in ordem if t in raizes],
        "filhos_de": filhos_de,
        "folhas": folhas,
        "multipais": multipais,
        "avisos": avisos,
        "hash": _hash(caminho),
    }


def rebuild(con, caminho: str = None) -> dict:
    """Aplica o arquivo no banco. Idempotente."""
    caminho = caminho or config.ARQUIVO_TAXONOMIA
    arv = carregar_arquivo(caminho)
    agora = db.agora()

    for tid in arv["ordem"]:
        a = arv["atributos"][tid]
        sin = a.get("sinonimos") or []
        if isinstance(sin, str):
            sin = [sin]
        con.execute(
            """INSERT INTO tag(id,nome_canonico,definicao,sinonimos,diagnostica,
                              transversal,criada_em)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 nome_canonico=excluded.nome_canonico,
                 definicao=excluded.definicao,
                 sinonimos=excluded.sinonimos,
                 diagnostica=excluded.diagnostica,
                 transversal=excluded.transversal,
                 ausente_do_arquivo=0""",
            (tid, a["nome"], a.get("definicao"), "; ".join(sin),
             1 if a.get("diagnostica", True) else 0,
             1 if a.get("transversal", False) else 0, agora),
        )

    # tag no banco ausente do arquivo: fila de "nao arquivadas", nunca apagada.
    presentes = set(arv["ordem"])
    orfas = []
    for r in con.execute("SELECT id FROM tag"):
        if r["id"] not in presentes:
            orfas.append(r["id"])
            con.execute("UPDATE tag SET ausente_do_arquivo=1 WHERE id=?", (r["id"],))

    con.execute("DELETE FROM tag_ancestral")
    con.executemany(
        "INSERT INTO tag_ancestral(tag_id,ancestral_id,distancia) VALUES(?,?,?)",
        [(t, a, d) for t, anc in arv["ancestrais"].items() for a, d in anc.items()],
    )
    db.gravar_meta(con, "taxonomia_hash", arv["hash"])
    db.gravar_meta(con, "taxonomia_rebuild_em", agora)
    con.commit()

    arv["orfas"] = orfas
    arv["total_tags"] = len(arv["ordem"])
    arv["total_arestas"] = sum(len(v) for v in arv["ancestrais"].values())
    return arv


if __name__ == "__main__":
    con = db.conectar()
    db.criar_schema(con)
    r = rebuild(con)
    print(f"{r['disciplina']}: {r['total_tags']} tags, {len(r['folhas'])} folhas, "
          f"{len(r['ordem']) - len(r['folhas'])} nos internos, "
          f"{len(r['multipais'])} tags com mais de um pai")
    for a in r["avisos"]:
        print("  aviso:", a)
    if r["orfas"]:
        print("  nao arquivadas (somem do YAML):", ", ".join(r["orfas"]))
