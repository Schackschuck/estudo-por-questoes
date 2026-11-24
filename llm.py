"""
Camada única e trocável de LLM (seção 13).

Todo acesso a modelo de linguagem do sistema passa por este arquivo. Ele é
usado em exatamente dois lugares, os dois fora da rotina diária:

  1. sugerir_tags()  — botão opcional na tela de importar;
  2. relatorio()     — quando eu peço um texto em linguagem natural.

Resolver questão, corrigir, calcular domínio e ordenar a trilha NÃO passam
por aqui. Se este arquivo for apagado, o app continua funcionando inteiro,
só perde o botão de sugestão e o relatório em prosa.

Para ligar: pip install anthropic  e  set ANTHROPIC_API_KEY=...
Sem isso, as funções devolvem {"ok": False, ...} e a interface esconde os
botões — nenhuma chamada é tentada.

Toda chamada é contada em meta['llm_chamadas'], para o critério de aceitação
"rodar uma sessão inteira com zero chamadas a LLM" ser verificável de fato.
"""
import os

MODELO = "claude-opus-5"


def disponivel() -> bool:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _chamar(con, system: str, prompt: str, max_tokens: int = 4000) -> str:
    import anthropic

    import db
    n = int(db.ler_meta(con, "llm_chamadas", "0")) + 1
    db.gravar_meta(con, "llm_chamadas", n)
    con.commit()

    cliente = anthropic.Anthropic()
    r = cliente.messages.create(
        model=MODELO,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in r.content if b.type == "text").strip()


SYSTEM_TAGS = """Você etiqueta questões de botânica contra uma árvore de conceitos fixa.
Responda SOMENTE com ids da lista fornecida, um por linha, no máximo 3 linhas.
A primeira linha é a tag principal (o conceito que a questão cobra).
As demais são secundárias: só inclua se a questão realmente exigir o conceito
para ser respondida. Etiqueta decorativa é ruído — na dúvida, devolva menos.
Nenhum texto além dos ids."""


def sugerir_tags(con, questao: dict, candidatas: list) -> dict:
    """
    candidatas: [{"id":..., "nome":..., "definicao":...}] — a árvore inteira.
    Devolve {"ok": True, "tags": [ids validados]}.
    """
    if not disponivel():
        return {"ok": False, "erro": "LLM desligado (sem ANTHROPIC_API_KEY ou sem o pacote anthropic)"}

    validos = {c["id"] for c in candidatas}
    catalogo = "\n".join(
        f"{c['id']} — {c['nome']}" + (f" ({c['definicao']})" if c.get("definicao") else "")
        for c in candidatas)
    alts = "\n".join(f"{a['letra']}) {a['texto']}" for a in questao.get("alternativas", []))
    prompt = (f"ÁRVORE DE CONCEITOS:\n{catalogo}\n\n"
              f"QUESTÃO:\n{questao['enunciado']}\n\n{alts}\n\n"
              f"Gabarito: {questao.get('gabarito') or '?'}")
    try:
        texto = _chamar(con, SYSTEM_TAGS, prompt, max_tokens=200)
    except Exception as e:
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}

    tags = [l.strip().split()[0] for l in texto.splitlines() if l.strip()]
    return {"ok": True, "tags": [t for t in tags if t in validos][:3]}


SYSTEM_RELATORIO = """Você escreve o relatório de estudo de uma pessoa a partir de
números já calculados. Não invente dados nem conceitos que não estão na entrada.
Seja direto: o que está fraco, por que o sistema acha isso, e o que fazer a seguir.
No máximo 300 palavras, em português do Brasil, sem bajulação e sem listas longas.
Se a evidência de alguma tag for pouca, diga isso em vez de afirmar fraqueza."""


def relatorio(con, dados: dict) -> dict:
    """dados: saída de modelo.fraquezas() + contexto. Uso ocasional, sob demanda."""
    if not disponivel():
        return {"ok": False, "erro": "LLM desligado (sem ANTHROPIC_API_KEY ou sem o pacote anthropic)"}
    import json
    try:
        texto = _chamar(con, SYSTEM_RELATORIO,
                        json.dumps(dados, ensure_ascii=False, indent=1), max_tokens=1500)
    except Exception as e:
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}
    return {"ok": True, "texto": texto}
