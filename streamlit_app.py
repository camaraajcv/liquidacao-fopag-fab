import streamlit as st
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

# -----------------------------
# Helpers
# -----------------------------
def dec_from_ptbr(s: str) -> Decimal:
    """
    Converte "1.234.567,89" ou "1234,56" ou "1234.56" -> Decimal
    """
    s = (s or "").strip()
    if not s:
        return Decimal("0.00")
    # remove espaços
    s = s.replace(" ", "")
    # se tem vírgula como decimal (pt-BR)
    if "," in s:
        s = s.replace(".", "")  # remove separador de milhar
        s = s.replace(",", ".") # troca decimal
    # mantém só número, sinal e ponto
    s = re.sub(r"[^0-9\.\-]", "", s)
    if s in ("", "-", "."):
        return Decimal("0.00")
    return Decimal(s)

def fmt_money(d: Decimal) -> str:
    # XML exige ponto decimal e sem milhar, com 2 casas
    q = d.quantize(Decimal("0.01"))
    return f"{q:.2f}"

def parse_tsv_lines(text: str):
    lines = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        lines.append(raw)
    return lines

def split_cols(line: str):
    # aceita TAB, ; ou múltiplos espaços
    if "\t" in line:
        cols = [c.strip() for c in line.split("\t")]
    elif ";" in line:
        cols = [c.strip() for c in line.split(";")]
    else:
        cols = re.split(r"\s{2,}", line.strip())
        cols = [c.strip() for c in cols if c.strip()]
    return cols

# -----------------------------
# Parsing inputs
# -----------------------------
def parse_pco_block(text: str):
    """
    Espera colunas:
    numEmpe | codSubItemEmpe | codSit | numClassA | valor
    """
    items_pos = []  # positivos
    items_neg = []  # negativos -> despesaAnular (valor absoluto)
    lines = parse_tsv_lines(text)
    for i, line in enumerate(lines, start=1):
        cols = split_cols(line)
        if len(cols) < 5:
            raise ValueError(f"PCO linha {i}: esperado 5 colunas. Recebido: {cols}")

        numEmpe = cols[0]
        codSub = cols[1].zfill(2)
        codSit = cols[2]
        numClassA = cols[3]
        val = dec_from_ptbr(cols[4])

        if val < 0:
            items_neg.append({
                "numEmpe": numEmpe,
                "codSubItemEmpe": codSub,
                "codSit": codSit,
                "numClassA": numClassA,
                "vlr": (-val)  # sem sinal no XML (como você pediu)
            })
        else:
            items_pos.append({
                "numEmpe": numEmpe,
                "codSubItemEmpe": codSub,
                "codSit": codSit,
                "numClassA": numClassA,
                "vlr": val
            })

    return items_pos, items_neg

def parse_pgto_block(text: str):
    """
    Espera colunas:
    CNPJ | BANCO | AGENCIA | txtCit | valor
    """
    rows = []
    lines = parse_tsv_lines(text)
    for i, line in enumerate(lines, start=1):
        cols = split_cols(line)
        if len(cols) < 5:
            raise ValueError(f"Pgto linha {i}: esperado 5 colunas. Recebido: {cols}")
        cnpj = re.sub(r"\D", "", cols[0])
        banco = cols[1].zfill(3)
        agencia = re.sub(r"\D", "", cols[2]).zfill(4)  # mantém 4, ajuste se precisar
        txtCit = cols[3]
        val = dec_from_ptbr(cols[4])
        rows.append({
            "cnpj": cnpj,
            "banco": banco,
            "agencia": agencia,
            "txtCit": txtCit,
            "vlr": val
        })
    return rows

def parse_outros_lanc(text: str):
    """
    NOVO formato (você corrigiu):
    situacao | class3 | class2 | NDD | valor

    - situacao: PRV001/PRV002/PRV003/LPA385/LPA386...
    - class3: classificação orçamentária classe 3 (sem separador, o usuário cola como vier)
    - class2: conta do passivo (classe 2)
    - NDD: para o centro de custo (vai em relOutrosLancamentos)
    - valor
    """
    rows = []
    lines = parse_tsv_lines(text)
    for i, line in enumerate(lines, start=1):
        cols = split_cols(line)
        if len(cols) < 5:
            raise ValueError(f"OutrosLanc linha {i}: esperado 5 colunas. Recebido: {cols}")
        situacao = cols[0].strip().upper()
        class3 = re.sub(r"\D", "", cols[1]) or cols[1].strip()
        class2 = re.sub(r"\D", "", cols[2]) or cols[2].strip()
        ndd = cols[3].strip()  # mantém como o usuário colar (ex: 31.90.11.37)
        val = dec_from_ptbr(cols[4])
        rows.append({
            "codSit": situacao,
            "class3": class3,
            "class2": class2,
            "ndd": ndd,
            "vlr": val
        })
    return rows

# -----------------------------
# XML builder (padrão DH001)
# -----------------------------
NS_SB = "http://www.tesouro.gov.br/siafi/submissao"
NS_DH = "http://services.docHabil.cpr.siafi.tesouro.fazenda.gov.br/"

ET.register_namespace("sb", NS_SB)
ET.register_namespace("dh", NS_DH)

def el(tag, text=None, ns=None):
    if ns:
        e = ET.Element(f"{{{ns}}}{tag}")
    else:
        e = ET.Element(tag)
    if text is not None:
        e.text = str(text)
    return e

def build_xml(data):
    """
    data: dict com todos os campos coletados.
    """
    arquivo = el("arquivo", ns=NS_SB)

    # header
    header = el("header", ns=NS_SB)
    header.append(el("codigoLayout", data["codigoLayout"], ns=NS_SB))
    header.append(el("dataGeracao", data["dataGeracao"], ns=NS_SB))
    header.append(el("sequencialGeracao", str(int(data["sequencialGeracao"])), ns=NS_SB))
    header.append(el("anoReferencia", data["anoReferencia"], ns=NS_SB))
    header.append(el("ugResponsavel", data["ugResponsavel"], ns=NS_SB))
    header.append(el("cpfResponsavel", data["cpfResponsavel"], ns=NS_SB))
    arquivo.append(header)

    detalhes = el("detalhes", ns=NS_SB)
    detalhe = el("detalhe", ns=NS_SB)
    detalhes.append(detalhe)
    arquivo.append(detalhes)

    cpr = el("CprDhCadastrar", ns=NS_DH)
    detalhe.append(cpr)

    cpr.append(el("codUgEmit", data["codUgEmit"]))
    cpr.append(el("anoDH", data["anoDH"]))
    cpr.append(el("codTipoDH", data["codTipoDH"]))

    # dadosBasicos
    db = el("dadosBasicos")
    db.append(el("dtEmis", data["dtEmis"]))
    db.append(el("dtVenc", data["dtVenc"]))
    db.append(el("codUgPgto", data["codUgPgto"]))
    db.append(el("vlr", fmt_money(data["vlr_liquido"])))
    db.append(el("txtObser", data["txtObser"]))
    db.append(el("txtProcesso", data["txtProcesso"]))
    db.append(el("dtAteste", data["dtAteste"]))
    db.append(el("codCredorDevedor", data["codCredorDevedor"]))
    db.append(el("dtPgtoReceb", data["dtPgtoReceb"]))

    docOrig = el("docOrigem")
    docOrig.append(el("codIdentEmit", data["codIdentEmit"]))
    docOrig.append(el("dtEmis", data["doc_dtEmis"]))
    docOrig.append(el("numDocOrigem", data["numDocOrigem"]))
    docOrig.append(el("vlr", fmt_money(data["vlr_liquido"])))
    db.append(docOrig)

    cpr.append(db)

    # PCO (grupo por codSit)
    # No XSD normalmente PCO tem: numSeqItem, codSit, codUgEmpe, lista pcoItem
    # Vamos agrupar por codSit (mantendo ordem de aparecimento).
    pco_by_sit = []
    seen = set()
    for it in data["pco_pos"]:
        k = it["codSit"]
        if k not in seen:
            seen.add(k)
            pco_by_sit.append(k)

    seq_pco = 0
    # map para centro de custo (numSeqPai -> lista de pcoItem seq)
    pco_seq_map = {}  # (seq_pco) -> list of (seq_item, value)

    for codSit in pco_by_sit:
        seq_pco += 1
        pco = el("pco")
        pco.append(el("numSeqItem", str(seq_pco)))
        pco.append(el("codSit", codSit))
        pco.append(el("codUgEmpe", data["codUgEmpe"]))

        seq_item = 0
        pco_seq_map[seq_pco] = []

        for it in [x for x in data["pco_pos"] if x["codSit"] == codSit]:
            seq_item += 1
            pi = el("pcoItem")
            # ORDEM: numSeqItem, numEmpe, codSubItemEmpe, vlr, numClassA
            # (o seu erro anterior mostrou que o XSD esperava vlr antes de indrLiquidado)
            pi.append(el("numSeqItem", str(seq_item)))
            pi.append(el("numEmpe", it["numEmpe"]))
            pi.append(el("codSubItemEmpe", it["codSubItemEmpe"]))
            pi.append(el("vlr", fmt_money(it["vlr"])))
            pi.append(el("numClassA", it["numClassA"]))
            pco.append(pi)

            pco_seq_map[seq_pco].append((seq_item, it["vlr"]))

        cpr.append(pco)

    # despesaAnular (agrupada por codSit também, conforme XSD)
    desp_by_sit = []
    seen2 = set()
    for it in data["pco_neg"]:
        k = it["codSit"]
        if k not in seen2:
            seen2.add(k)
            desp_by_sit.append(k)

    seq_desp = 0
    desp_seq_map = {}  # (seq_desp) -> list (seq_item, val)
    for codSit in desp_by_sit:
        seq_desp += 1
        da = el("despesaAnular")
        da.append(el("numSeqItem", str(seq_desp)))
        da.append(el("codSit", codSit))
        da.append(el("codUgEmpe", data["codUgEmpe"]))

        seq_item = 0
        desp_seq_map[seq_desp] = []

        for it in [x for x in data["pco_neg"] if x["codSit"] == codSit]:
            seq_item += 1
            dai = el("despesaAnularItem")
            dai.append(el("numSeqItem", str(seq_item)))
            dai.append(el("numEmpe", it["numEmpe"]))
            dai.append(el("codSubItemEmpe", it["codSubItemEmpe"]))
            dai.append(el("vlr", fmt_money(it["vlr"])))
            dai.append(el("numClassA", it["numClassA"]))
            da.append(dai)

            desp_seq_map[seq_desp].append((seq_item, it["vlr"]))

        cpr.append(da)

    # outrosLancamentos (opcional)
    if data["outros"]:
        outros = el("outrosLancamentos")
        # cada item: codSit, class3, class2, vlr
        seq_ol = 0
        outros_seq_map = []  # list (seq_item, vlr) para centro custo relOutrosLancamentos
        for r in data["outros"]:
            seq_ol += 1
            oli = el("outrosLancamentosItem")
            oli.append(el("numSeqItem", str(seq_ol)))
            oli.append(el("codSit", r["codSit"]))
            # nomes exatos podem variar conforme XSD; aqui usamos os campos mais prováveis:
            # class3 = despesa (classe 3), class2 = passivo (classe 2)
            oli.append(el("numClassE", r["class3"]))  # ajuste se seu XSD chama diferente
            oli.append(el("numClassD", r["class2"]))  # ajuste se seu XSD chama diferente
            oli.append(el("vlr", fmt_money(r["vlr"])))
            outros.append(oli)
            outros_seq_map.append((seq_ol, r["vlr"], r["ndd"]))
        cpr.append(outros)
    else:
        outros_seq_map = []

    # centroCusto
    cc = el("centroCusto")
    cc.append(el("numSeqItem", "1"))
    cc.append(el("codCentroCusto", data["codCentroCusto"]))
    cc.append(el("mesReferencia", data["mesReferencia"]))
    cc.append(el("anoReferencia", data["anoReferenciaCC"]))
    cc.append(el("codUgBenef", data["codUgBenef"]))
    cc.append(el("codSIORG", data["codSIORG"]))

    # relPcoItem: referencia cada pco/pcoItem por numSeqPai (seq do PCO) e numSeqItem (seq do item)
    for pai, items in pco_seq_map.items():
        for (seq_item, v) in items:
            rpi = el("relPcoItem")
            rpi.append(el("numSeqPai", str(pai)))
            rpi.append(el("numSeqItem", str(seq_item)))
            rpi.append(el("vlr", fmt_money(v)))
            cc.append(rpi)

    # relDespesaAnular: referencia despesaAnular/despesaAnularItem
    for pai, items in desp_seq_map.items():
        for (seq_item, v) in items:
            rda = el("relDespesaAnular")
            rda.append(el("numSeqPai", str(pai)))
            rda.append(el("numSeqItem", str(seq_item)))
            rda.append(el("vlr", fmt_money(v)))
            cc.append(rda)

    # relOutrosLancamentos: usa NDD (como você pediu) + vincula item outros
    # Como o nome/estrutura exata depende do XSD, aqui vai um formato comum:
    # relOutrosLancamentos: numSeqItem (item outros) + ndd + vlr
    if outros_seq_map:
        for (seq_item, v, ndd) in outros_seq_map:
            rol = el("relOutrosLancamentos")
            rol.append(el("numSeqItem", str(seq_item)))
            rol.append(el("ndd", ndd))
            rol.append(el("vlr", fmt_money(v)))
            cc.append(rol)

    cpr.append(cc)

    # dadosPgto: múltiplos blocos
    for pg in data["pgto"]:
        dp = el("dadosPgto")
        dp.append(el("codCredorDevedor", pg["cnpj"]))
        dp.append(el("vlr", fmt_money(pg["vlr"])))
        predoc = el("predoc")
        predoc.append(el("txtObser", data["txtObserPgto"]))
        predocOB = el("predocOB")
        predocOB.append(el("codTipoOB", data["codTipoOB"]))
        predocOB.append(el("codCredorDevedor", pg["cnpj"]))
        predocOB.append(el("txtCit", pg["txtCit"]))

        favo = el("numDomiBancFavo")
        favo.append(el("banco", pg["banco"]))
        favo.append(el("agencia", pg["agencia"]))
        favo.append(el("conta", data["contaFavo"]))
        predocOB.append(favo)

        pgto = el("numDomiBancPgto")
        pgto.append(el("banco", data["bancoPgto"]))
        pgto.append(el("conta", data["contaPgto"]))
        predocOB.append(pgto)

        predoc.append(predocOB)
        dp.append(predoc)
        cpr.append(dp)

    # trailler
    trailler = el("trailler", ns=NS_SB)
    trailler.append(el("quantidadeDetalhe", "1", ns=NS_SB))
    arquivo.append(trailler)

    return arquivo

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Gerador DH001 (FOPAG)", layout="wide")
st.title("Gerador de XML DH001 (FOPAG) — COMAER / SIAFI")

tabs = st.tabs([
    "Dados Básicos",
    "PCO (colar)",
    "Dados Pgto (colar)",
    "Outros Lançamentos (colar)",
    "Centro de Custo",
    "Gerar XML"
])

today = date.today()

# Estado
if "pco_text" not in st.session_state:
    st.session_state.pco_text = ""
if "pgto_text" not in st.session_state:
    st.session_state.pgto_text = ""
if "outros_text" not in st.session_state:
    st.session_state.outros_text = ""

with tabs[0]:
    st.subheader("Dados Básicos")
    c1, c2, c3 = st.columns(3)

    with c1:
        codigoLayout = st.text_input("codigoLayout", value="DH001", key="db_codigoLayout")
        ugResponsavel = st.text_input("ugResponsavel", value="120052", key="db_ugResp")
        cpfResponsavel = st.text_input("cpfResponsavel", value="09857528740", key="db_cpfResp")
    with c2:
        dataGeracao = st.text_input("dataGeracao (DD/MM/AAAA)", value=today.strftime("%d/%m/%Y"), key="db_dataGeracao")
        sequencialGeracao = st.text_input("sequencialGeracao (sem zero à esquerda)", value="1", key="db_seqGer")
        anoReferencia = st.text_input("anoReferencia", value=str(today.year), key="db_anoRef")
    with c3:
        codUgEmit = st.text_input("codUgEmit", value="120052", key="db_codUgEmit")
        anoDH = st.text_input("anoDH", value=str(today.year), key="db_anoDH")
        codTipoDH = st.text_input("codTipoDH", value="FL", key="db_codTipoDH")

    st.divider()
    c4, c5, c6 = st.columns(3)
    with c4:
        dtEmis = st.text_input("dtEmis (AAAA-MM-DD)", value=today.strftime("%Y-%m-%d"), key="db_dtEmis")
        dtVenc = st.text_input("dtVenc (AAAA-MM-DD)", value=today.strftime("%Y-%m-%d"), key="db_dtVenc")
        codUgPgto = st.text_input("codUgPgto", value="120052", key="db_codUgPgto")
    with c5:
        txtObser = st.text_input("txtObser", value="PAGAMENTO DA FOPAG JANEIRO/2026 CIVIL", key="db_txtObser")
        txtProcesso = st.text_input("txtProcesso", value="67420.000835/2026-37", key="db_txtProc")
        dtAteste = st.text_input("dtAteste (AAAA-MM-DD)", value=today.strftime("%Y-%m-%d"), key="db_dtAteste")
    with c6:
        codCredorDevedor = st.text_input("codCredorDevedor (UG)", value="120052", key="db_codCredDev")
        dtPgtoReceb = st.text_input("dtPgtoReceb (AAAA-MM-DD)", value=today.strftime("%Y-%m-%d"), key="db_dtPgtoReceb")
        codIdentEmit = st.text_input("docOrigem.codIdentEmit", value="120052", key="db_doc_codIdentEmit")

    c7, c8, c9 = st.columns(3)
    with c7:
        doc_dtEmis = st.text_input("docOrigem.dtEmis (AAAA-MM-DD)", value=today.strftime("%Y-%m-%d"), key="db_doc_dtEmis")
    with c8:
        numDocOrigem = st.text_input("docOrigem.numDocOrigem", value="FOPAG.CIVL.JAN", key="db_doc_numDoc")
    with c9:
        st.info("O valor líquido (vlr) será calculado automaticamente a partir do PCO/DespesaAnular.")

with tabs[1]:
    st.subheader("PCO — cole linhas do Excel")
    st.caption("Formato por linha: numEmpe<TAB>subitem<TAB>codSit<TAB>numClassA<TAB>valor  | valor negativo vira DespesaAnular automaticamente.")
    st.session_state.pco_text = st.text_area(
        "Cole aqui o PCO",
        value=st.session_state.pco_text,
        height=240,
        key="pco_text_area"
    )
    st.text("Exemplo:")
    st.code("2026NE000055\t46\tDFL033\t113110105\t45905,55\n2026NE000055\t46\tAFL033\t113110105\t-31381,26", language="text")

with tabs[2]:
    st.subheader("Dados de Pagamento — cole linhas do Excel")
    st.caption("Formato por linha: CNPJ<TAB>BANCO<TAB>AGENCIA<TAB>txtCit<TAB>valor")
    st.session_state.pgto_text = st.text_area(
        "Cole aqui os pagamentos (dadosPgto)",
        value=st.session_state.pgto_text,
        height=240,
        key="pgto_text_area"
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        txtObserPgto = st.text_input("predoc.txtObser", value="PAGAMENTO FOPAG", key="pg_txtObser")
    with c2:
        codTipoOB = st.text_input("predocOB.codTipoOB", value="OBF", key="pg_codTipoOB")
    with c3:
        contaFavo = st.text_input("numDomiBancFavo.conta", value="FOPAG", key="pg_contaFavo")

    c4, c5 = st.columns(2)
    with c4:
        bancoPgto = st.text_input("numDomiBancPgto.banco", value="002", key="pg_bancoPgto")
    with c5:
        contaPgto = st.text_input("numDomiBancPgto.conta", value="UNICA", key="pg_contaPgto")

with tabs[3]:
    st.subheader("Outros Lançamentos — opcional (pode ficar em branco)")
    st.caption("Formato por linha: situacao<TAB>class3<TAB>class2<TAB>NDD<TAB>valor")
    st.session_state.outros_text = st.text_area(
        "Cole aqui Outros Lançamentos",
        value=st.session_state.outros_text,
        height=220,
        key="outros_text_area"
    )
    st.text("Exemplo:")
    st.code("PRV001\t311110100\t211110101\t31.90.11.37\t223,89", language="text")
    st.info("Se você não colar nada aqui, a tag <outrosLancamentos> NÃO será gerada no XML.")

with tabs[4]:
    st.subheader("Centro de Custo")
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        codCentroCusto = st.text_input("codCentroCusto", value="221A00", key="cc_codCentro")
    with col2:
        mesReferencia = st.text_input("mesReferencia (MM)", value="01", key="cc_mesRef")
    with col3:
        anoReferenciaCC = st.text_input("anoReferencia (CC)", value=str(today.year), key="cc_anoRef")
    with col4:
        codUgBenef = st.text_input("codUgBenef", value="120052", key="cc_codUgBenef")
    with col5:
        codSIORG = st.text_input("codSIORG", value="2332", key="cc_codSiorg")

    st.caption("Obs: o app monta automaticamente relPcoItem, relDespesaAnular e relOutrosLancamentos (com NDD).")

with tabs[5]:
    st.subheader("Gerar XML + Checagens")

    # Parse e checagens
    try:
        pco_pos, pco_neg = parse_pco_block(st.session_state.pco_text) if st.session_state.pco_text.strip() else ([], [])
        pgto = parse_pgto_block(st.session_state.pgto_text) if st.session_state.pgto_text.strip() else []
        outros = parse_outros_lanc(st.session_state.outros_text) if st.session_state.outros_text.strip() else []

        total_pos = sum((x["vlr"] for x in pco_pos), Decimal("0.00"))
        total_neg = sum((x["vlr"] for x in pco_neg), Decimal("0.00"))  # já está absoluto
        vlr_liquido = (total_pos - total_neg).quantize(Decimal("0.01"))

        soma_pgto = sum((x["vlr"] for x in pgto), Decimal("0.00")).quantize(Decimal("0.01"))
        total_outros = sum((x["vlr"] for x in outros), Decimal("0.00")).quantize(Decimal("0.01"))

        soma_centro = (total_pos + total_outros - total_neg).quantize(Decimal("0.01"))

        st.markdown("### Checagens")
        st.write(f"**Total POS (PCO):** {fmt_money(total_pos)}")
        st.write(f"**Total NEG (DespesaAnular):** {fmt_money(total_neg)}")
        st.write(f"**Valor líquido (Dados Básicos):** {fmt_money(vlr_liquido)}")
        st.write(f"**Soma Pagamentos (dadosPgto):** {fmt_money(soma_pgto)}")
        st.write(f"**Total OutrosLanc:** {fmt_money(total_outros)}")
        st.write(f"**Soma CentroCusto (relPco + relOutros - relDespesaAnular):** {fmt_money(soma_centro)}")

        if soma_pgto != vlr_liquido:
            st.warning("⚠️ Soma de pagamentos NÃO bate com o valor líquido (dadosBasicos).")
        else:
            st.success("✅ Soma de pagamentos bate com o valor líquido (dadosBasicos).")

        # Monta dados p/ XML
        payload = {
            "codigoLayout": codigoLayout,
            "dataGeracao": dataGeracao,
            "sequencialGeracao": sequencialGeracao,
            "anoReferencia": anoReferencia,
            "ugResponsavel": ugResponsavel,
            "cpfResponsavel": cpfResponsavel,
            "codUgEmit": codUgEmit,
            "anoDH": anoDH,
            "codTipoDH": codTipoDH,

            "dtEmis": dtEmis,
            "dtVenc": dtVenc,
            "codUgPgto": codUgPgto,
            "vlr_liquido": vlr_liquido,
            "txtObser": txtObser,
            "txtProcesso": txtProcesso,
            "dtAteste": dtAteste,
            "codCredorDevedor": codCredorDevedor,
            "dtPgtoReceb": dtPgtoReceb,

            "codIdentEmit": codIdentEmit,
            "doc_dtEmis": doc_dtEmis,
            "numDocOrigem": numDocOrigem,

            "codUgEmpe": codUgEmit,  # normalmente é a UG que emite/empenha; ajuste se precisar
            "pco_pos": pco_pos,
            "pco_neg": pco_neg,

            "pgto": pgto,
            "txtObserPgto": txtObserPgto,
            "codTipoOB": codTipoOB,
            "contaFavo": contaFavo,
            "bancoPgto": bancoPgto,
            "contaPgto": contaPgto,

            "outros": outros,

            "codCentroCusto": codCentroCusto,
            "mesReferencia": mesReferencia,
            "anoReferenciaCC": anoReferenciaCC,
            "codUgBenef": codUgBenef,
            "codSIORG": codSIORG,
        }

        gerar = st.button("Gerar XML", key="btn_gerar_xml")

        if gerar:
            root = build_xml(payload)
            xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            st.download_button(
                "Baixar XML (.xml)",
                data=xml_bytes,
                file_name="DH001_FOPAG.xml",
                mime="application/xml",
                key="dl_xml"
            )
            st.code(xml_bytes.decode("utf-8"), language="xml")

    except Exception as e:
        st.error(f"Erro ao processar: {e}")