'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const el = (t, cls, txt) => { const e = document.createElement(t);
  if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

async function api(rota, corpo) {
  const r = await fetch(rota, corpo === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corpo)});
  return r.json();
}

let ARVORE = null;                 // {tags, filhos, raizes}
const S = {                        // estado da sessão em andamento
  sessao: null, modo: 'estudo', questao: null, eventos: [], t0: 0,
  marcada: null, eliminadas: new Set(), tPrimeiraElim: null,
  confianca: null, confiancaDerivada: false, vistas: [], travada: false,
  pontos: 0, premiados: new Set(),
};

// Marcar é só a escolha; eliminar é o raciocínio, e é ele que vira peso,
// crédito parcial e matriz de confusão. A recompensa segue o valor do dado.
const PONTOS = {marcar: 1, eliminar: 3};

function premiar(alvo, pontos, forte) {
  if (S.modo === 'simulado') return;   // prova cronometrada não tem plaquinha
  S.pontos += pontos;
  $('#pontos').textContent = S.pontos + ' pt';
  const r = alvo.getBoundingClientRect();
  const e = el('div', 'premio' + (forte ? ' forte' : ''), '+' + pontos);
  e.style.left = (r.left + r.width / 2) + 'px';
  e.style.top = (r.top + 8) + 'px';
  document.body.appendChild(e);
  setTimeout(() => e.remove(), 850);
  if (navigator.vibrate) { try { navigator.vibrate(forte ? 16 : 7); } catch (_) {} }
}

// Só a PRIMEIRA vez que cada alternativa recebe cada gesto rende ponto:
// marcar e desmarcar em looping não farma nada, e o dado não se distorce.
function premiarUmaVez(chave, alvo, pontos, forte) {
  if (S.premiados.has(chave)) return;
  S.premiados.add(chave);
  premiar(alvo, pontos, forte);
}

// ───────────────────────────────────────────────────────────── navegação
$$('#nav button').forEach(b => b.onclick = () => {
  $$('#nav button').forEach(x => x.classList.toggle('ativo', x === b));
  $$('.tela').forEach(t => t.classList.remove('ativa'));
  $('#tela-' + b.dataset.tela).classList.add('ativa');
  if (b.dataset.tela === 'painel') carregarPainel();
  if (b.dataset.tela === 'curadoria') carregarCuradoria();
});

async function carregarArvore() {
  if (!ARVORE) {
    ARVORE = await api('/api/arvore');
    const dl = $('#lista-tags');
    dl.innerHTML = '';
    Object.values(ARVORE.tags)
      .sort((a, b) => a.nome_canonico.localeCompare(b.nome_canonico))
      .forEach(t => { const o = el('option'); o.value = t.id;
                      o.label = t.nome_canonico; dl.appendChild(o); });
  }
  return ARVORE;
}

// ═══════════════════════════════════════════════════════════════ RESOLVER

$('#btn-comecar').onclick = async () => {
  const modo = document.querySelector('input[name=modo]:checked').value;
  const r = await api('/api/sessao', {modo, tags_alvo: $('#filtro-tag').value || null});
  S.sessao = r.sessao_id; S.modo = modo; S.vistas = []; S.pontos = 0;
  $('#pontos').textContent = modo === 'simulado' ? '' : '0 pt';
  $('#inicio-sessao').classList.add('oculto');
  $('#area-questao').classList.remove('oculto');
  $('#info-sessao').textContent = 'modo ' + modo;
  proxima();
};

$('#btn-encerrar').onclick = () => {
  S.sessao = null;
  $('#area-questao').classList.add('oculto');
  $('#sem-questao').classList.add('oculto');
  $('#inicio-sessao').classList.remove('oculto');
  ARVORE = null;
};

async function proxima() {
  const tag = $('#filtro-tag').value.trim();
  const p = new URLSearchParams({sessao: S.sessao, evitar: S.vistas.join(',')});
  if (tag) p.set('tag', tag);
  const q = await api('/api/proxima?' + p);
  $('#correcao').classList.add('oculto');
  $('#pos-confirmacao').classList.add('oculto');
  if (q.fim) {
    $('#area-questao').classList.add('oculto');
    $('#sem-questao').classList.remove('oculto');
    return;
  }
  S.questao = q; S.eventos = []; S.marcada = null; S.eliminadas = new Set();
  S.tPrimeiraElim = null; S.confianca = null; S.confiancaDerivada = false;
  S.travada = false; S.premiados = new Set();
  S.t0 = performance.now();
  desenharQuestao();
}

const dt = () => +((performance.now() - S.t0) / 1000).toFixed(2);

function desenharQuestao() {
  const q = S.questao;
  $('#procedencia').innerHTML = '';
  if (q.assunto_novo)
    $('#procedencia').appendChild(el('span', 'bandeira', 'assunto novo'));
  const info = [q.procedencia,
      q.exposicoes_anteriores ? `você já viu esta questão ${q.exposicoes_anteriores}×` : '']
      .filter(Boolean).join(' · ');
  if (info) $('#procedencia').appendChild(el('span', '', ' ' + info));
  $('#enunciado').textContent = q.enunciado;

  const box = $('#alternativas'); box.innerHTML = '';
  q.alternativas.forEach(a => {
    const linha = el('div', 'alt'); linha.dataset.id = a.id; linha.dataset.letra = a.letra;
    const corpo = el('button', 'corpo');
    corpo.appendChild(el('span', 'letra', a.letra + ')'));
    corpo.appendChild(el('span', '', a.texto));
    corpo.onclick = () => marcar(a, linha);
    const riscar = el('button', 'riscar', '✗');
    riscar.title = 'eliminar alternativa';
    riscar.onclick = () => eliminar(a, linha);
    linha.append(corpo, riscar);
    box.appendChild(linha);
  });
  $('#metas').classList.remove('oculto');
  $('#btn-confirmar').classList.remove('oculto');
  $('#btn-confirmar').disabled = true;
}

function marcar(a, linha) {
  if (S.travada) return;
  if (S.marcada && S.marcada.id !== a.id)
    S.eventos.push({tipo: 'trocou', alternativa_id: a.id, t: dt()});
  else
    S.eventos.push({tipo: 'marcou', alternativa_id: a.id, t: dt()});
  S.marcada = a;
  $$('#alternativas .alt').forEach(x => x.classList.toggle('marcada', x === linha));
  premiarUmaVez('marcar:' + a.id, linha, PONTOS.marcar, false);
  atualizarConfirmar();
}

function eliminar(a, linha) {
  if (S.travada) return;
  if (S.eliminadas.has(a.id)) {
    S.eliminadas.delete(a.id);
    S.eventos.push({tipo: 'desmarcou_eliminacao', alternativa_id: a.id, t: dt()});
  } else {
    S.eliminadas.add(a.id);
    if (S.tPrimeiraElim === null) S.tPrimeiraElim = dt();
    S.eventos.push({tipo: 'eliminou', alternativa_id: a.id, t: dt()});
  }
  linha.classList.toggle('eliminada', S.eliminadas.has(a.id));
  if (S.eliminadas.has(a.id))
    premiarUmaVez('eliminar:' + a.id, linha, PONTOS.eliminar, true);
  atualizarConfirmar();
}

function atualizarConfirmar() {
  $('#btn-confirmar').disabled = !S.marcada;
}

// Riscar todas as outras é, ele mesmo, a declaração de confiança: o raciocínio
// já está explícito na sequência de eliminações, com timestamp. Perguntar de
// novo nessa situação é atrito puro, sem dado novo.
function eliminouTodasAsOutras() {
  if (!S.questao || !S.marcada) return false;
  if (S.eliminadas.has(S.marcada.id)) return false;
  return S.questao.alternativas.every(
    a => a.id === S.marcada.id || S.eliminadas.has(a.id));
}

$('#btn-confirmar').onclick = () => {
  if (!S.marcada) return;
  S.eventos.push({tipo: 'confirmou', alternativa_id: S.marcada.id, t: dt()});
  S.travada = true;
  // Princípio da seção 8: o que não é comportamento natural de prova acontece
  // DEPOIS de confirmar e ANTES de ver o gabarito.
  if (S.modo !== 'triagem') return enviar({});
  if (eliminouTodasAsOutras()) {
    S.confiancaDerivada = true;
    // Valor próprio, não 'certeza': o sistema grava que a confiança foi
    // DERIVADA da eliminação, e nunca inventa uma declaração que você não fez.
    return enviar({confianca: 'eliminacao_completa'});
  }
  abrirPosConfirmacao();
};

function abrirPosConfirmacao() {
  $('#pos-confirmacao').classList.remove('oculto');
  $('#pergunta-confianca').classList.remove('oculto');
  $('#pergunta-chute').classList.add('oculto');
  $('#pos-confirmacao').scrollIntoView({behavior: 'smooth', block: 'center'});
}

$$('#pergunta-confianca button').forEach(b => b.onclick = () => {
  S.confianca = b.dataset.conf;
  $('#pergunta-confianca').classList.add('oculto');
  const suspeito = S.eliminadas.size === 0 && dt() < 4;
  if (suspeito) $('#pergunta-chute').classList.remove('oculto');
  else enviar({confianca: S.confianca});
});

$$('#pergunta-chute button').forEach(b => b.onclick = () => {
  if (b.dataset.chute === 'sim') S.confianca = 'chute';
  enviar({confianca: S.confianca});
});

$$('#metas button').forEach(b => b.onclick = () => {
  if (S.travada) return;
  S.travada = true;
  enviar({meta_resposta: b.dataset.meta});
});

async function enviar(extra) {
  const corpo = Object.assign({
    questao_id: S.questao.id, sessao_id: S.sessao,
    alternativa: S.marcada ? S.marcada.letra : null,
    eventos: S.eventos, tempo_total: dt(),
    tempo_primeira_eliminacao: S.tPrimeiraElim,
  }, extra);
  const r = await api('/api/tentativa', corpo);
  S.vistas.push(S.questao.id);
  mostrarCorrecao(r);
}

function mostrarCorrecao(r) {
  $('#pos-confirmacao').classList.add('oculto');
  $('#metas').classList.add('oculto');
  $('#btn-confirmar').classList.add('oculto');

  if (r.descartada) {
    // "Já fiz essa" / "não preciso": não conta como acerto nem erro.
    $('#veredito').className = 'neutro';
    $('#veredito').textContent = 'Descartada — o domínio não mudou.';
    $('#explicacao').textContent = '';
  } else {
    $$('#alternativas .alt').forEach(x => {
      if (x.dataset.letra === r.gabarito) x.classList.add('certa');
      else if (x.classList.contains('marcada')) x.classList.add('errada');
    });
    $('#veredito').className = r.resultado;
    $('#veredito').textContent = r.resultado === 'acerto'
      ? 'Acertou.' : `Errou. Gabarito: ${r.gabarito}.`;
    $('#explicacao').textContent = r.explicacao || '';
  }

  const notas = [];
  if (S.confiancaDerivada)
    notas.push('Você riscou todas as outras — a confiança não foi perguntada, '
             + 'porque a eliminação já registrou o seu raciocínio.');
  if (r.riscou_a_correta && r.resultado === 'erro')
    notas.push('Você riscou justamente a alternativa certa. Isso não é uma lacuna: '
             + 'é uma concepção errada, e você agiu sobre ela. A tentativa pesa '
             + `${r.peso_multiplicador}× — escolher errado e rejeitar o certo são `
             + 'dois fatos, não um. Volte ao material antes de fazer mais questões '
             + 'desta tag.');
  if (r.discriminacao > 0) {
    const pct = Math.round(r.discriminacao * 100);
    notas.push(r.resultado === 'acerto'
      ? `Você riscou ${pct}% dos distratores e acertou: o raciocínio ficou `
        + `explícito, então esta tentativa pesa ${r.peso_multiplicador}× no cálculo.`
      : `Você riscou ${pct}% dos distratores corretamente antes de errar: `
        + `reduziu o campo, e isso vale um crédito parcial de `
        + `${Math.round(r.credito_parcial * 100)}% em vez de zero.`);
  }
  if (r.tipo_de_erro === 'lacuna')
    notas.push('Classificado como lacuna: volte ao material, não a mais questões.');
  if (r.tipo_de_erro === 'equivoco')
    notas.push('Classificado como equívoco: errado com convicção, não por falta de dado.');
  if (r.tipo_de_erro === 'descuido')
    notas.push('Classificado como descuido: você domina a tag, errou rápido e com certeza. Sem tratamento.');
  if (r.dominio_ilusorio)
    notas.push('Acerto sem eliminação, rápido e com baixa confiança: domínio ilusório. Entra para reteste espaçado.');
  if (r.suspeita_chute) notas.push('Tentativa marcada como suspeita de chute.');
  if (r.quarentena) notas.push('Esta questão foi para a quarentena: ' + r.quarentena);
  $('#diagnostico').textContent = notas.join('\n');

  $('#correcao').classList.remove('oculto');
  $('#correcao').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

$('#btn-proxima').onclick = proxima;

// ═══════════════════════════════════════════════════════════════ PAINEL

async function carregarPainel() {
  ARVORE = null;
  const [p, a] = await Promise.all([api('/api/painel'), carregarArvore()]);

  const t = p.totais;
  $('#resumo').innerHTML =
    `<h2>Resumo</h2><p class="dica">${t.ativas} questões ativas · ${t.tentativas} tentativas
     contadas · ${t.descartadas} descartadas · ${t.quarentena} em quarentena ·
     ${t.sessoes} sessões</p>
     <p class="dica">Chamadas a LLM até agora: <b>${p.llm_chamadas}</b>
     (a rotina de estudo não faz nenhuma). Hash da árvore: <code>${p.hash || '—'}</code></p>`;

  const hoje = $('#hoje'); hoje.innerHTML = '';
  if (!p.hoje.length) {
    hoje.innerHTML = '<p class="dica">Ainda não há evidência suficiente. ' +
      'Responda algumas questões e volte aqui.</p>';
  }
  p.hoje.forEach((f, i) => {
    const d = el('div', 'item-hoje');
    const cru = f.tentativas ? `${f.acertos}/${f.tentativas} acertos · ` : '';
    d.innerHTML = `<b>${i + 1}. ${f.nome}</b>
      <span class="pilula ${f.situacao}" title="domínio estimado, não taxa de acerto">
        ${cru}domínio ${Math.round(f.nivel * 100)}%</span>
      ${f.medida ? '' : '<span class="provisorio">provisório, pouca evidência</span>'}`;
    const ul = el('ul');
    f.justificativa.forEach(j => ul.appendChild(el('li', '', j)));
    d.appendChild(ul);
    hoje.appendChild(d);
  });
  $('#btn-relatorio').classList.toggle('oculto', !p.llm_ligado || !p.hoje.length);

  desenharPais(p.pais || []);

  const al = $('#alertas'); al.innerHTML = '';
  if (p.confusao && p.confusao.length) {
    const d = el('div', 'cartao');
    d.innerHTML = '<h2>Você troca um pelo outro</h2><p class="dica">Vem da tag no '
      + 'distrator: não é só "errou a tag", é <b>com o que</b> confundiu. '
      + 'A força desconta as vezes em que você riscou esse mesmo conceito.</p>';
    p.confusao.forEach(c => {
      const i = el('div', 'item-hoje');
      i.innerHTML = `<b>${c.nome_de} → ${c.nome_para}</b>
        <span class="pilula ${c.forca >= 0.6 ? 'fraca' : 'parcial'}">força ${c.forca}</span>
        ${c.mutua ? '<span class="provisorio">confusão mútua</span>' : ''}
        <ul><li>${c.frase}</li>${c.distinguiu
          ? `<li>você riscou ${c.nome_para} corretamente ${c.distinguiu}×</li>` : ''}</ul>`;
      d.appendChild(i);
    });
    al.appendChild(d);
  }
  p.coladas.forEach(c => {
    const d = el('div', 'cartao aviso');
    d.innerHTML = `<h3>Tags coladas: ${c.nome_a} × ${c.nome_b}</h3>
      <p class="dica">Correlação de coocorrência ${c.correlacao} em ${c.juntas_em} questões.
      ${c.sugestao}</p>`;
    al.appendChild(d);
  });
  const cal = p.calibracao_secundaria;
  if (cal.suficiente) {
    const d = el('div', 'cartao aviso');
    d.innerHTML = `<h3>Peso da tag secundária pode ser calibrado</h3>
      <p class="dica">Hoje: ${cal.atual}. Sugerido pelo seu próprio dado:
      <b>${cal.sugerido}</b>. ${cal.motivo}</p>`;
    al.appendChild(d);
  }

  desenharArvore(a);
}

// Os conteudos-pai: a mesma evidencia da lista de cima, um nivel acima da
// arvore. Fechado por padrao -- a setinha e o gesto de "me mostra as tags".
function desenharPais(pais) {
  const box = $('#pais'); box.innerHTML = '';
  if (!pais.length) {
    box.innerHTML = '<p class="dica">Nenhum ramo tem folhas medidas suficientes '
      + 'ainda. Um ramo só entra aqui com pelo menos duas folhas respondidas — '
      + 'com uma só, "conteúdo-pai fraco" seria a folha com outro nome.</p>';
    return;
  }
  pais.forEach((x, i) => {
    const d = el('div', 'item-hoje pai');
    const rot = el('div', 'rotulo-pai');
    const caret = el('span', 'caret', '▸');
    const nome = el('span', 'nome-pai');
    nome.innerHTML = (x.caminho && x.caminho.length
        ? `<span class="caminho">${x.caminho.join(' › ')} › </span>` : '')
      + `<b>${i + 1}. ${x.nome}</b>`;
    const pil = el('span', 'pilula ' + x.situacao,
      `domínio ${Math.round(x.nivel * 100)}%`);
    pil.title = `Domínio estimado do ramo, herdado das folhas — não é a média `
      + `das porcentagens nem taxa de acerto.
Peso de evidência: ${x.peso}.`;
    const conta = el('span', 'conta-folhas',
      `${x.folhas_fracas}/${x.folhas_medidas} folhas fracas`
      + (x.folhas_sem_dado ? ` · ${x.folhas_sem_dado} sem dado` : ''));
    rot.append(caret, nome, pil, conta);
    if (!x.medida) rot.appendChild(el('span', 'provisorio', 'provisório, pouca evidência'));
    d.appendChild(rot);

    const corpo = el('div', 'corpo-pai oculto');
    const ul = el('ul');
    x.justificativa.forEach(j => ul.appendChild(el('li', '', j)));
    corpo.appendChild(ul);
    const lista = el('div', 'folhas-pai');
    x.folhas.forEach(f => {
      const li = el('div', 'folha');
      const cru = f.tentativas ? `${f.acertos}/${f.tentativas} · ` : '';
      li.innerHTML = `<span class="nome">${f.nome}</span>
        <span class="pilula ${f.situacao}" title="domínio estimado nesta tag; cru: `
        + `${f.acertos}/${f.tentativas} acertos, peso ${f.peso}">`
        + `${cru}${Math.round(f.nivel * 100)}%</span>`;
      const ir = el('button', 'link ir', 'estudar');
      ir.title = 'começar uma sessão só nesta tag';
      ir.onclick = () => irParaTag(f.tag_id);
      li.appendChild(ir);
      lista.appendChild(li);
    });
    corpo.appendChild(lista);
    d.appendChild(corpo);

    const alterna = () => { corpo.classList.toggle('oculto');
      caret.textContent = corpo.classList.contains('oculto') ? '▸' : '▾'; };
    rot.onclick = alterna;   // a linha inteira abre, nao so a setinha
    box.appendChild(d);
  });
}

// Clicar numa folha da lista leva direto a uma sessao restrita aquela tag:
// diagnostico que nao vira acao e so um grafico bonito.
function irParaTag(tagId) {
  $('#filtro-tag').value = tagId;
  $$('#nav button').forEach(b => b.classList.toggle('ativo', b.dataset.tela === 'resolver'));
  $$('.tela').forEach(t => t.classList.remove('ativa'));
  $('#tela-resolver').classList.add('ativa');
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function desenharArvore(a) {
  const box = $('#arvore'); box.innerHTML = '';
  const soComDado = $('#so-com-dado').checked;

  const no = (id, nivel) => {
    const t = a.tags[id]; if (!t) return null;
    const filhos = (a.filhos[id] || []).slice()
      .sort((x, y) => a.tags[x].nome_canonico.localeCompare(a.tags[y].nome_canonico));
    const kids = filhos.map(f => no(f, nivel + 1)).filter(Boolean);
    if (soComDado && t.peso < 1 && !kids.length) return null;

    const d = el('div', 'no' + (t.excluida ? ' excluida' : ''));
    const rot = el('div', 'rotulo');
    const caret = el('span', 'caret', kids.length ? (nivel < 1 ? '▾' : '▸') : '');
    const nome = el('span', 'nome', t.nome_canonico);
    const pil = el('span', 'pilula ' + t.situacao,
      t.situacao === 'sem_dado' ? `— · ${t.questoes}q`
        : `${Math.round(t.nivel * 100)}% · peso ${t.peso.toFixed(1)}`);
    pil.title = t.situacao === 'sem_dado'
      ? `${t.questoes} questão(ões) no banco, nenhuma resposta ainda`
      : `Domínio estimado, não taxa de acerto.
`
        + `Cru: ${t.acertos}/${t.tentativas} acertos nesta tag.
`
        + `Estimativa = (acertos + 1) / (peso + 2), partindo de 50%.
`
        + `Peso de evidência: ${t.peso.toFixed(2)}.`;
    rot.append(caret, nome, pil);
    d.appendChild(rot);

    if (kids.length) {
      const cx = el('div', 'filhos');
      kids.forEach(k => cx.appendChild(k));
      if (nivel >= 1) cx.classList.add('oculto');
      d.appendChild(cx);
      const alterna = () => { cx.classList.toggle('oculto');
        caret.textContent = cx.classList.contains('oculto') ? '▸' : '▾'; };
      caret.onclick = alterna; nome.onclick = alterna;
    } else {
      nome.onclick = async () => {
        if (!confirm(t.excluida
            ? `Voltar "${t.nome_canonico}" para o escopo de estudo?`
            : `Tirar "${t.nome_canonico}" (e o ramo abaixo) do escopo de estudo?`)) return;
        await api('/api/escopo', {tag_id: id, excluir: !t.excluida});
        carregarPainel();
      };
    }
    return d;
  };
  a.raizes.forEach(r => { const n = no(r, 0); if (n) box.appendChild(n); });
}

$('#so-com-dado').onchange = () => carregarArvore().then(desenharArvore);

$('#btn-relatorio').onclick = async () => {
  $('#relatorio').textContent = 'escrevendo…';
  const r = await api('/api/relatorio');
  $('#relatorio').textContent = r.ok ? r.texto : 'Não deu: ' + r.erro;
};

// ═══════════════════════════════════════════════════════════════ IMPORTAR

let RASCUNHO = [];

$('#btn-analisar').onclick = async () => {
  await carregarArvore();
  const r = await api('/api/importar/analisar', {texto: $('#texto-bruto').value});
  RASCUNHO = r.questoes.map(q => Object.assign(q, {tags: []}));
  desenharRevisao();
};

function desenharRevisao() {
  const box = $('#revisao'); box.innerHTML = '';
  if (!RASCUNHO.length) {
    box.innerHTML = '<div class="cartao"><p class="dica">Nada reconhecido. ' +
      'Confira se as alternativas começam com “a)”, “b)”…</p></div>';
    return;
  }
  RASCUNHO.forEach((q, i) => box.appendChild(cartaoQuestao(q, i)));
  const salvar = el('button', 'primario', `Salvar ${RASCUNHO.length} questão(ões)`);
  salvar.onclick = salvarLote;
  box.appendChild(salvar);
}

function cartaoQuestao(q, i) {
  const d = el('div', 'qcard');
  const cab = el('div', 'cabeca');
  cab.innerHTML = `<span>Questão ${i + 1}</span><span>
    ${q.enunciado_negativo ? '<span class="bandeira">enunciado negativo</span>' : ''}
    ${q.pista_formal ? '<span class="bandeira">pista formal</span>' : ''}</span>`;
  d.appendChild(cab);

  const ta = el('textarea'); ta.rows = 3; ta.value = q.enunciado;
  ta.oninput = () => q.enunciado = ta.value;
  d.appendChild(ta);

  const alts = el('div', 'alts');
  q.alternativas.forEach(a => {
    const inp = el('input'); inp.value = `${a.letra}) ${a.texto}`;
    inp.oninput = () => a.texto = inp.value.replace(/^\s*\(?[a-eA-E]\)?[\).\-:]?\s*/, '');
    alts.appendChild(inp);
  });
  d.appendChild(alts);

  const lg = el('label', 'campo'); lg.textContent = 'Gabarito';
  const sel = el('select');
  q.alternativas.forEach(a => { const o = el('option', '', a.letra); o.value = a.letra;
    if (q.gabarito === a.letra) o.selected = true; sel.appendChild(o); });
  if (!q.gabarito && q.alternativas.length) q.gabarito = q.alternativas[0].letra;
  sel.onchange = () => q.gabarito = sel.value;
  lg.appendChild(sel); d.appendChild(lg);

  const chips = el('div', 'tags-escolhidas');
  const redesenhaChips = () => {
    chips.innerHTML = '';
    q.tags.forEach((t, k) => {
      const c = el('span', 'chip' + (k ? ' secundaria' : ''));
      c.append(el('span', '', (k ? '' : '★ ') + (ARVORE.tags[t]?.nome_canonico || t)));
      const x = el('button', '', '×');
      x.onclick = () => { q.tags.splice(k, 1); redesenhaChips(); };
      c.appendChild(x); chips.appendChild(c);
    });
    if (!q.tags.length) chips.appendChild(el('span', 'dica',
      'sem tag — a questão não será salva. A primeira que você escolher é a principal.'));
  };
  redesenhaChips();
  d.appendChild(chips);

  const busca = el('input');
  busca.placeholder = 'adicionar tag (até 3 por questão)';
  busca.setAttribute('list', 'lista-tags');
  busca.onchange = () => {
    const v = busca.value.trim();
    if (ARVORE.tags[v] && !q.tags.includes(v) && q.tags.length < 3) q.tags.push(v);
    busca.value = ''; redesenhaChips();
  };
  d.appendChild(busca);

  const acoes = el('div', 'botoes');
  const sug = el('button', '', 'Sugerir tags (LLM)');
  sug.onclick = async () => {
    sug.textContent = 'pensando…';
    const r = await api('/api/importar/sugerir', {questao: q});
    sug.textContent = 'Sugerir tags (LLM)';
    if (!r.ok) { alert(r.erro); return; }
    r.tags.forEach(t => { if (!q.tags.includes(t) && q.tags.length < 3) q.tags.push(t); });
    redesenhaChips();
  };
  const rm = el('button', '', 'Descartar esta');
  rm.onclick = () => { RASCUNHO.splice(RASCUNHO.indexOf(q), 1); desenharRevisao(); };
  acoes.append(sug, rm);
  d.appendChild(acoes);
  return d;
}

async function salvarLote() {
  const proc = $('#procedencia-lote').value.trim();
  RASCUNHO.forEach(q => { if (proc && !q.procedencia) q.procedencia = proc; });
  const r = await api('/api/importar/salvar', {questoes: RASCUNHO});
  const avisos = (r.coladas || []).map(c =>
    `⚠ ${c.nome_a} × ${c.nome_b} sempre aparecem juntas (${c.correlacao}). ${c.sugestao}`);
  $('#resultado-import').textContent =
    [`${r.salvas} questão(ões) salva(s).`, ...avisos].join('\n');
  const naoSalvas = RASCUNHO.filter(q => !q.tags.length || !q.gabarito).length;
  if (naoSalvas) $('#resultado-import').textContent +=
    `\n${naoSalvas} ficaram de fora por falta de tag ou gabarito.`;
  RASCUNHO = []; $('#revisao').innerHTML = ''; $('#texto-bruto').value = '';
  ARVORE = null;
}

// ═══════════════════════════════════════════════════════════════ CURADORIA

async function carregarCuradoria() {
  const r = await api('/api/curadoria');
  const box = $('#fila-curadoria'); box.innerHTML = '';
  if (!r.questoes.length) {
    box.innerHTML = '<div class="cartao"><p class="dica">Fila vazia.</p></div>';
    return;
  }
  r.questoes.forEach(q => {
    const d = el('div', 'qcard');
    d.innerHTML = `<div class="cabeca"><span>#${q.id} · ${q.estado}</span>
      <span>${q.erros}/${q.tentativas} erros</span></div>
      <p class="dica">${q.motivo || ''}</p>`;

    const ta = el('textarea'); ta.rows = 3; ta.value = q.enunciado;
    d.appendChild(ta);
    const lista = el('div', 'alts');
    q.alternativas.forEach(a => lista.appendChild(
      el('div', 'dica', `${a.letra}) ${a.texto}`)));
    d.appendChild(lista);

    const lg = el('label', 'campo'); lg.textContent = 'Gabarito correto';
    const sel = el('select');
    q.alternativas.forEach(a => { const o = el('option', '', a.letra); o.value = a.letra;
      if (q.gabarito === a.letra) o.selected = true; sel.appendChild(o); });
    lg.appendChild(sel); d.appendChild(lg);

    const exp = el('textarea'); exp.rows = 2; exp.placeholder = 'explicação (opcional)';
    exp.value = q.explicacao || ''; d.appendChild(exp);

    const acoes = el('div', 'botoes');
    const corrigir = el('button', '', 'Corrigir e reativar');
    corrigir.onclick = async () => {
      await api('/api/curadoria/' + q.id, {enunciado: ta.value, gabarito: sel.value,
        explicacao: exp.value, estado: 'ativa'});
      carregarCuradoria();
    };
    const aposentar = el('button', '', 'Aposentar');
    aposentar.onclick = async () => {
      await api('/api/curadoria/' + q.id, {estado: 'aposentada'});
      carregarCuradoria();
    };
    acoes.append(corrigir, aposentar);
    d.appendChild(acoes);
    box.appendChild(d);
  });
}

carregarArvore();
