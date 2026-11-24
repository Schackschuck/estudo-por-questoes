"""
Importação por texto colado (seções 7 e 8).

O parser não precisa acertar sempre: a tela de importar mostra o que ele
separou para revisão antes de salvar. Ele só precisa poupar digitação.

Também faz a checagem aritmética de pista formal (seção 6.3), sem LLM.
"""
import re
import statistics

import config

# "a)" "A." "(c)" "d -" "e )"
RE_ALT = re.compile(r"^\s*\(?\s*([a-eA-E])\s*[\)\.\-–—:]\s*(.+?)\s*$")
# "1." "12)" "QUESTÃO 3" "Questao 3 -"
RE_NUM = re.compile(r"^\s*(?:quest[ãa]o\s*)?(\d{1,3})\s*[\)\.\-–—:]\s*(.*)$", re.I)
RE_GAB_LINHA = re.compile(r"^\s*(?:gabarito|resposta|resp\.?)\s*[:\-]?\s*\(?([a-eA-E])\)?\s*$", re.I)
RE_GAB_LISTA = re.compile(r"(\d{1,3})\s*[\-–—:\.]\s*([a-eA-E])\b")
RE_BANCA = re.compile(r"\(([^()]{3,60}?(?:19|20)\d{2}[^()]{0,20})\)")
RE_BANCA2 = re.compile(r"^\s*\(([A-ZÁÂÃÉÊÍÓÔÕÚÇ][^()]{2,40})\)\s*")

NEGATIVO = [
    "incorreta", "incorreto", "inCORRETA", "não é", "nao e", "não corresponde",
    "exceto", "errada", "errado", "falsa", "falso", "não está", "inadequada",
    "não pode ser", "não se aplica",
]


def _eh_negativo(enunciado: str) -> bool:
    e = enunciado.lower()
    return any(p in e for p in NEGATIVO)


def _extrair_procedencia(enunciado: str):
    """Devolve (enunciado_limpo, procedencia). Pega '(UFRGS 2019)' do começo."""
    m = RE_BANCA.search(enunciado[:120]) or RE_BANCA2.match(enunciado)
    if not m:
        return enunciado, None
    proc = m.group(1).strip()
    if enunciado[:120].startswith(m.group(0)):
        enunciado = enunciado[len(m.group(0)):].strip()
    return enunciado, proc


def detectar_pista_formal(alternativas, gabarito) -> bool:
    """
    Se a alternativa correta é muito mais longa que a mediana das outras, a
    questão entrega a resposta pela forma. Não é motivo de quarentena: é
    variável de controle no modelo do aluno (seção 6.3).
    """
    if not gabarito or len(alternativas) < 3:
        return False
    correta = next((a for a in alternativas if a["letra"].upper() == gabarito.upper()), None)
    if not correta:
        return False
    outras = [len(a["texto"]) for a in alternativas if a is not correta]
    if not outras:
        return False
    mediana = statistics.median(outras)
    return mediana > 0 and len(correta["texto"]) > config.PISTA_FORMAL_RAZAO * mediana


def separar(texto: str) -> list:
    """Quebra um bloco de texto colado em questões estruturadas."""
    linhas = [l.rstrip() for l in texto.replace("\r", "").split("\n")]

    # Um bloco final do tipo "GABARITO: 1-A 2-C 3-B" vale para o lote todo.
    gabaritos_lote = {}
    cauda = "\n".join(linhas[-8:])
    if re.search(r"gabarito", cauda, re.I) and len(RE_GAB_LISTA.findall(cauda)) >= 2:
        for num, letra in RE_GAB_LISTA.findall(cauda):
            gabaritos_lote[int(num)] = letra.upper()

    questoes = []
    atual = None

    def fechar():
        nonlocal atual
        if atual and atual["alternativas"]:
            enun = "\n".join(atual["enunciado"]).strip()
            enun, proc = _extrair_procedencia(enun)
            gab = atual["gabarito"] or gabaritos_lote.get(atual["numero"])
            atual.update(enunciado=enun, gabarito=gab, procedencia=proc,
                         enunciado_negativo=_eh_negativo(enun))
            atual["pista_formal"] = detectar_pista_formal(atual["alternativas"], gab)
            questoes.append(atual)
        atual = None

    for linha in linhas:
        if not linha.strip():
            continue

        g = RE_GAB_LINHA.match(linha)
        if g and atual:
            atual["gabarito"] = g.group(1).upper()
            continue

        alt = RE_ALT.match(linha)
        num = RE_NUM.match(linha)

        # "a) ..." só é alternativa se já existe enunciado aberto e a letra
        # vem na sequência esperada — evita comer um item "a)" do enunciado.
        if alt and atual and atual["enunciado"]:
            letra = alt.group(1).upper()
            esperada = chr(ord("A") + len(atual["alternativas"]))
            if letra == esperada:
                atual["alternativas"].append({"letra": letra, "texto": alt.group(2).strip()})
                continue

        if num and (atual is None or atual["alternativas"]):
            fechar()
            atual = {"numero": int(num.group(1)), "enunciado": [num.group(2)],
                     "alternativas": [], "gabarito": None}
            continue

        if atual is None:
            atual = {"numero": len(questoes) + 1, "enunciado": [linha],
                     "alternativas": [], "gabarito": None}
        elif atual["alternativas"]:
            # continuação da última alternativa (quebra de linha no meio)
            atual["alternativas"][-1]["texto"] += " " + linha.strip()
        else:
            atual["enunciado"].append(linha)

    fechar()
    return questoes
