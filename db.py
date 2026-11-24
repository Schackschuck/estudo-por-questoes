"""Schema e conexao. SQLite, arquivo unico, sem ORM."""
import sqlite3
from datetime import datetime, timezone

import config

SCHEMA = """
PRAGMA foreign_keys = ON;

-- A arvore NAO vive aqui. tag e plano: sem coluna tag_pai (secao 5).
CREATE TABLE IF NOT EXISTS tag(
    id             TEXT PRIMARY KEY,
    nome_canonico  TEXT NOT NULL,
    definicao      TEXT,
    sinonimos      TEXT,
    diagnostica    INTEGER NOT NULL DEFAULT 1,
    -- ramo transversal: agrupa folhas que já vivem nos ramos temáticos,
    -- logo não conta como cobertura do conteúdo programático (seção 5)
    transversal    INTEGER NOT NULL DEFAULT 0,
    criada_em      TEXT NOT NULL,
    -- tag que existe no banco mas sumiu do YAML: fila de "nao arquivadas"
    ausente_do_arquivo INTEGER NOT NULL DEFAULT 0
);

-- Derivada e descartavel. Regenerada a cada rebuild do YAML.
CREATE TABLE IF NOT EXISTS tag_ancestral(
    tag_id      TEXT NOT NULL,
    ancestral_id TEXT NOT NULL,
    distancia   INTEGER NOT NULL,
    PRIMARY KEY (tag_id, ancestral_id)
);
CREATE INDEX IF NOT EXISTS ix_anc_ancestral ON tag_ancestral(ancestral_id);

CREATE TABLE IF NOT EXISTS questao(
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    enunciado          TEXT NOT NULL,
    gabarito           TEXT NOT NULL,
    explicacao         TEXT,
    enunciado_negativo INTEGER NOT NULL DEFAULT 0,
    pista_formal       INTEGER NOT NULL DEFAULT 0,
    procedencia        TEXT,
    estado             TEXT NOT NULL DEFAULT 'ativa',  -- ativa|quarentena|aposentada
    motivo_quarentena  TEXT,
    serve_para_triagem INTEGER NOT NULL DEFAULT 1,
    exposicoes         INTEGER NOT NULL DEFAULT 0,
    criada_em          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questao_tag(
    questao_id INTEGER NOT NULL REFERENCES questao(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tag(id),
    papel      TEXT NOT NULL,      -- principal | secundaria
    peso       REAL NOT NULL,
    PRIMARY KEY (questao_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_qt_tag ON questao_tag(tag_id);

CREATE TABLE IF NOT EXISTS alternativa(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id INTEGER NOT NULL REFERENCES questao(id) ON DELETE CASCADE,
    letra      TEXT NOT NULL,
    texto      TEXT NOT NULL,
    correta    INTEGER NOT NULL DEFAULT 0,
    -- v0.3: nascem presentes e nulas, para nao migrar schema depois (secao 6.2)
    tag_concorrente_id    TEXT,
    confusao_representada TEXT
);
CREATE INDEX IF NOT EXISTS ix_alt_questao ON alternativa(questao_id);

CREATE TABLE IF NOT EXISTS sessao(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    modo      TEXT NOT NULL,      -- triagem | estudo | simulado
    inicio    TEXT NOT NULL,
    fim       TEXT,
    tags_alvo TEXT
);

CREATE TABLE IF NOT EXISTS tentativa(
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id             INTEGER NOT NULL REFERENCES questao(id) ON DELETE CASCADE,
    sessao_id              INTEGER REFERENCES sessao(id),
    alternativa_escolhida  TEXT,
    meta_resposta          TEXT,   -- ja_fiz | nao_preciso | nao_faco_ideia
    resultado              TEXT NOT NULL,  -- acerto | erro | descartada
    tipo_de_erro           TEXT,   -- lacuna | conceitual | descuido
                                   -- | equivoco (riscou a alternativa certa)
    confianca_declarada    TEXT,   -- certeza | duvida_entre_duas | chute
                                   -- | eliminacao_completa (derivada: riscou
                                   --   todas as outras, não foi perguntada)
    tempo_total            REAL,
    tempo_ate_primeira_eliminacao REAL,
    exposicoes_anteriores  INTEGER NOT NULL DEFAULT 0,
    peso                   REAL NOT NULL DEFAULT 1.0,
    suspeita_chute         INTEGER NOT NULL DEFAULT 0,
    dominio_ilusorio       INTEGER NOT NULL DEFAULT 0,
    criada_em              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tent_questao ON tentativa(questao_id);

-- A tabela que alimenta o modelo do aluno.
CREATE TABLE IF NOT EXISTS evento(
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tentativa_id       INTEGER NOT NULL REFERENCES tentativa(id) ON DELETE CASCADE,
    tipo               TEXT NOT NULL,  -- eliminou | desmarcou_eliminacao | marcou | trocou | confirmou
    alternativa_id     INTEGER,
    timestamp_relativo REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evt_tentativa ON evento(tentativa_id);

CREATE TABLE IF NOT EXISTS dominio_tag(
    tag_id       TEXT PRIMARY KEY REFERENCES tag(id),
    nivel        REAL NOT NULL,
    incerteza    REAL NOT NULL,
    peso_total   REAL NOT NULL,   -- depois da atribuição por cruzamento
    peso_bruto   REAL NOT NULL DEFAULT 0,  -- antes: mede COBERTURA, não domínio
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escopo_tag(
    tag_id     TEXT PRIMARY KEY REFERENCES tag(id),
    excluida_em TEXT NOT NULL
);

-- Fase 7. Nasce vazia.
CREATE TABLE IF NOT EXISTS agendamento(
    tag_id          TEXT PRIMARY KEY REFERENCES tag(id),
    proxima_revisao TEXT,
    intervalo_atual INTEGER
);

-- hash do YAML junto de qualquer relatorio, para separar "aprendi" de
-- "editei o YAML" (secao 5).
CREATE TABLE IF NOT EXISTS meta(
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conectar(caminho: str = None) -> sqlite3.Connection:
    con = sqlite3.connect(caminho or config.ARQUIVO_BANCO)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def criar_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    # dominio_tag é derivada: basta acrescentar a coluna nova em bancos antigos.
    cols = {r[1] for r in con.execute("PRAGMA table_info(dominio_tag)")}
    if "peso_bruto" not in cols:
        con.execute("ALTER TABLE dominio_tag ADD COLUMN peso_bruto REAL NOT NULL DEFAULT 0")
    if "transversal" not in {r[1] for r in con.execute("PRAGMA table_info(tag)")}:
        con.execute("ALTER TABLE tag ADD COLUMN transversal INTEGER NOT NULL DEFAULT 0")
    con.commit()


def ler_meta(con, chave, padrao=None):
    r = con.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else padrao


def gravar_meta(con, chave, valor):
    con.execute(
        "INSERT INTO meta(chave,valor) VALUES(?,?) "
        "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
        (chave, str(valor)),
    )
