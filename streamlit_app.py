# app.py
import re
import io
import textwrap
import datetime as dt
import streamlit as st
import xml.etree.ElementTree as ET
from xml.dom import minidom
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# =========================
# Helpers
# =========================

SB_NS = "http://www.tesouro.gov.br/siafi/submissao"
DH_NS = "http://services.docHabil.cpr.siafi.tesouro.fazenda.gov.br/"

NSMAP = {"sb": SB_NS, "dh": DH_NS}
ET.register_namespace("sb", SB_NS)
ET.register_namespace("dh", DH_NS)

def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def norm_money_to_decimal(raw: str) -> Decimal:
    """
    Accepts values like:
      45905,55
      -31.381,26
      1.207.892,90
      64010.22
    Converts to Decimal with '.' decimal.
    """
    if raw is None:
        raise InvalidOperation("None")
    s = str(raw).strip()
    if not s:
        raise InvalidOperation("empty")
    # Remove spaces
    s = s.replace(" ", "")
    # If it has comma, treat comma as decimal separator (pt-BR) and remove thousand dots
    if "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    return Decimal(s)

def fmt_decimal(d: Decimal) -> str:
    # two decimals, dot separator, no thousands
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(q, "f")

def parse_paste_table(text: str, expected_cols: int):
    """
    Generic paste parser:
    - splits by lines
    - columns separated by TAB, ';', ',', or multiple spaces
    - returns list of lists (cols)
    """
    rows = []
    if not text or not text.strip():
        return rows

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        # Try TAB first (best for Excel paste)
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        else:
            # fallback: split by ';' or multiple spaces
            if ";" in line:
                parts = [p.strip() for p in line.split(";")]
            else:
                parts = re.split(r"\s{2,}|\s+\|\s+|\s+\|\s*|\s*\|\s+|\s+", line)
                parts = [p.strip() for p in parts if p.strip()]

        # If user pasted with commas in money and also used comma as delimiter,
        # this can explode columns. We keep it simple: require expected columns.
        if len(parts) < expected_cols:
            # try a softer split with single spaces only if still too few
            parts = re.split(r"\s+", line)
            parts = [p.strip() for p in parts if p.strip()]

        if len(parts) != expected_cols:
            raise ValueError(
                f"Linha inválida (esperado {expected_cols} colunas, veio {len(parts)}): {line}"
            )

        rows.append(parts)
    return rows

# =========================
# Domain parsing
# =========================

def parse_pco_block(pco_text: str):
    """
    Input columns: numEmpe | codSubItemEmpe | codSit | numClassA | vlr
    Negative vlr => despesaAnular (abs), without sign.
    Returns:
      pco_groups: list[{codSit, items:[{numEmpe, subitem, classA, vlr}]}] (vlr positive)
      da_groups: list[{codSit, items:[{numEmpe, subitem, classA, vlr_abs}]}] (vlr positive abs)
      totals: pos_sum, neg_abs_sum, net
    """
    rows = parse_paste_table(pco_text, 5)
    pos = []
    neg = []
    for (numEmpe, subitem, codSit, numClassA, vlr_raw) in rows:
        v = norm_money_to_decimal(vlr_raw)
        item = {
            "numEmpe": numEmpe.strip(),
            "subitem": subitem.strip().zfill(2),
            "codSit": codSit.strip(),
            "numClassA": only_digits(numClassA),
            "vlr": abs(v),
            "sign": (v < 0),
        }
        if v < 0:
            neg.append(item)
        else:
            pos.append(item)

    def group_by_codSit(items):
        groups = {}
        for it in items:
            groups.setdefault(it["codSit"], []).append(it)
        # stable order by codSit then input order
        out = []
        for codSit in sorted(groups.keys()):
            out.append({"codSit": codSit, "items": groups[codSit]})
        return out

    pco_groups = group_by_codSit(pos)
    da_groups = group_by_codSit(neg)

    pos_sum = sum([it["vlr"] for it in pos], Decimal("0.00"))
    neg_abs_sum = sum([it["vlr"] for it in neg], Decimal("0.00"))
    net = pos_sum - neg_abs_sum

    return pco_groups, da_groups, pos_sum, neg_abs_sum, net

def parse_pgto_block(pgto_text: str):
    """
    Input columns: CNPJ | BANCO | AGENCIA | txtCit | valor
    Returns list of dicts.
    """
    rows = parse_paste_table(pgto_text, 5)
    out = []
    for (cnpj, banco, agencia, txtCit, valor) in rows:
        out.append({
            "cnpj": only_digits(cnpj),
            "banco": only_digits(banco).zfill(3),
            "agencia": only_digits(agencia),
            "txtCit": txtCit.strip(),
            "vlr": norm_money_to_decimal(valor).copy_abs()
        })
    return out

def parse_outros_lanc(outros_text: str):
    """
    Input columns: codSit | class3 | class2 | NDD | valor
    Returns list of dicts (optional, can be empty).
    """
    if not outros_text or not outros_text.strip():
        return []
    rows = parse_paste_table(outros_text, 5)
    out = []
    for (codSit, class3, class2, ndd, valor) in rows:
        v = norm_money_to_decimal(valor)
        if v < 0:
            # safest: force user to keep provisão positiva
            v = v.copy_abs()
        out.append({
            "codSit": codSit.strip(),
            "class3": only_digits(class3),  # numClassA
            "class2": only_digits(class2),  # numClassD
            "ndd": ndd.strip(),              # not an XML field; kept for future CC logic
            "vlr": v
        })
    return out

# =========================
# XML builder (DH001-like)
# =========================

def prettify_xml(elem: ET.Element) -> str:
    rough = ET.tostring(elem, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

def build_xml(
    header: dict,
    basicos: dict,
    pco_groups: list,
    da_groups: list,
    outros_list: list,
    centro: dict,
    pgto_list: list,
):
    """
    Builds:
      <sb:arquivo>
        <sb:header>...</sb:header>
        <sb:detalhes>
          <sb:detalhe>
            <dh:CprDhCadastrar>...</dh:CprDhCadastrar>
          </sb:detalhe>
        </sb:detalhes>
        <sb:trailler>...</sb:trailler>
      </sb:arquivo>
    """

    root = ET.Element(ET.QName(SB_NS, "arquivo"))

    # Header
    h = ET.SubElement(root, ET.QName(SB_NS, "header"))
    for tag in ["codigoLayout", "dataGeracao", "sequencialGeracao", "anoReferencia", "ugResponsavel", "cpfResponsavel"]:
        ET.SubElement(h, ET.QName(SB_NS, tag)).text = str(header[tag])

    detalhes = ET.SubElement(root, ET.QName(SB_NS, "detalhes"))
    detalhe = ET.SubElement(detalhes, ET.QName(SB_NS, "detalhe"))

    cpr = ET.SubElement(detalhe, ET.QName(DH_NS, "CprDhCadastrar"))

    ET.SubElement(cpr, "codUgEmit").text = basicos["codUgEmit"]
    ET.SubElement(cpr, "anoDH").text = basicos["anoDH"]
    ET.SubElement(cpr, "codTipoDH").text = basicos["codTipoDH"]

    # dadosBasicos
    db = ET.SubElement(cpr, "dadosBasicos")
    for tag in ["dtEmis", "dtVenc", "codUgPgto", "vlr", "txtObser", "txtProcesso", "dtAteste", "codCredorDevedor", "dtPgtoReceb"]:
        ET.SubElement(db, tag).text = str(basicos[tag])

    doc = ET.SubElement(db, "docOrigem")
    ET.SubElement(doc, "codIdentEmit").text = basicos["doc_codIdentEmit"]
    ET.SubElement(doc, "dtEmis").text = basicos["doc_dtEmis"]
    ET.SubElement(doc, "numDocOrigem").text = basicos["doc_numDocOrigem"]
    ET.SubElement(doc, "vlr").text = basicos["doc_vlr"]

    # PCO groups
    # Structure (as in your V8): pco has: numSeqItem, codSit, codUgEmpe, then repeated pcoItem
    pco_seq = 0
    for grp in pco_groups:
        pco_seq += 1
        pco = ET.SubElement(cpr, "pco")
        ET.SubElement(pco, "numSeqItem").text = str(pco_seq)
        ET.SubElement(pco, "codSit").text = grp["codSit"]
        ET.SubElement(pco, "codUgEmpe").text = basicos["codUgEmit"]

        item_seq = 0
        for it in grp["items"]:
            item_seq += 1
            pi = ET.SubElement(pco, "pcoItem")
            ET.SubElement(pi, "numSeqItem").text = str(item_seq)
            ET.SubElement(pi, "numEmpe").text = it["numEmpe"]
            ET.SubElement(pi, "codSubItemEmpe").text = it["subitem"]
            ET.SubElement(pi, "vlr").text = fmt_decimal(it["vlr"])
            ET.SubElement(pi, "numClassA").text = it["numClassA"]

    # despesaAnular groups (from NEGATIVE lines in PCO)
    # Structure (as in your valid January file): despesaAnular has numSeqItem, codSit, codUgEmpe, then despesaAnularItem...
    da_seq = 0
    for grp in da_groups:
        da_seq += 1
        da = ET.SubElement(cpr, "despesaAnular")
        ET.SubElement(da, "numSeqItem").text = str(da_seq)
        ET.SubElement(da, "codSit").text = grp["codSit"]
        ET.SubElement(da, "codUgEmpe").text = basicos["codUgEmit"]

        item_seq = 0
        for it in grp["items"]:
            item_seq += 1
            dai = ET.SubElement(da, "despesaAnularItem")
            ET.SubElement(dai, "numSeqItem").text = str(item_seq)
            ET.SubElement(dai, "numEmpe").text = it["numEmpe"]
            ET.SubElement(dai, "codSubItemEmpe").text = it["subitem"]
            ET.SubElement(dai, "vlr").text = fmt_decimal(it["vlr"])  # abs, no negative sign
            ET.SubElement(dai, "numClassA").text = it["numClassA"]

    # outrosLanc (OPTIONAL)
    # Type requires: numSeqItem, codSit, (optional indrLiquidado), vlr, ... and classification fields.
    # We'll set numClassA=class3 and numClassD=class2 as you requested.
    if outros_list:
        ol_seq = 0
        for ol in outros_list:
            ol_seq += 1
            olx = ET.SubElement(cpr, "outrosLanc")
            ET.SubElement(olx, "numSeqItem").text = str(ol_seq)
            ET.SubElement(olx, "codSit").text = ol["codSit"]
            ET.SubElement(olx, "vlr").text = fmt_decimal(ol["vlr"])
            # class3 -> numClassA
            if ol["class3"]:
                ET.SubElement(olx, "numClassA").text = ol["class3"]
            # class2 -> numClassD
            if ol["class2"]:
                ET.SubElement(olx, "numClassD").text = ol["class2"]

    # centroCusto
    cc = ET.SubElement(cpr, "centroCusto")
    ET.SubElement(cc, "numSeqItem").text = "1"
    ET.SubElement(cc, "codCentroCusto").text = centro["codCentroCusto"]
    ET.SubElement(cc, "mesReferencia").text = centro["mesReferencia"]
    ET.SubElement(cc, "anoReferencia").text = centro["anoReferencia"]
    ET.SubElement(cc, "codUgBenef").text = centro["codUgBenef"]
    if centro.get("codSIORG"):
        ET.SubElement(cc, "codSIORG").text = centro["codSIORG"]

    # relations: relPcoItem, relDespesaAnular, relOutrosLanc
    # relPcoItem uses numSeqPai=pco.numSeqItem and numSeqItem=pcoItem.numSeqItem
    # We'll reconstruct the same sequencing used above.
    # Build indexes:
    # pco index: list of (pco_seq, items_count, item_values)
    pco_seq = 0
    for grp in pco_groups:
        pco_seq += 1
        item_seq = 0
        for it in grp["items"]:
            item_seq += 1
            rel = ET.SubElement(cc, "relPcoItem")
            ET.SubElement(rel, "numSeqPai").text = str(pco_seq)
            ET.SubElement(rel, "numSeqItem").text = str(item_seq)
            ET.SubElement(rel, "vlr").text = fmt_decimal(it["vlr"])

    # relDespesaAnular uses numSeqPai=despesaAnular.numSeqItem and numSeqItem=despesaAnularItem.numSeqItem
    da_seq = 0
    for grp in da_groups:
        da_seq += 1
        item_seq = 0
        for it in grp["items"]:
            item_seq += 1
            rel = ET.SubElement(cc, "relDespesaAnular")
            ET.SubElement(rel, "numSeqPai").text = str(da_seq)
            ET.SubElement(rel, "numSeqItem").text = str(item_seq)
            ET.SubElement(rel, "vlr").text = fmt_decimal(it["vlr"])

    # relOutrosLanc uses RelValor too. OutrosLanc only has numSeqItem (no item list),
    # so we set numSeqPai = outrosLanc.numSeqItem and numSeqItem = 1 (convention).
    if outros_list:
        for i, ol in enumerate(outros_list, start=1):
            rel = ET.SubElement(cc, "relOutrosLanc")
            ET.SubElement(rel, "numSeqPai").text = str(i)
            ET.SubElement(rel, "numSeqItem").text = "1"
            ET.SubElement(rel, "vlr").text = fmt_decimal(ol["vlr"])

    # dadosPgto (repeatable)
    for pg in pgto_list:
        dpg = ET.SubElement(cpr, "dadosPgto")
        ET.SubElement(dpg, "codCredorDevedor").text = pg["cnpj"]
        ET.SubElement(dpg, "vlr").text = fmt_decimal(pg["vlr"])

        predoc = ET.SubElement(dpg, "predoc")
        ET.SubElement(predoc, "txtObser").text = basicos.get("txtObserPredoc", "PAGAMENTO FOPAG")
        predocOB = ET.SubElement(predoc, "predocOB")
        ET.SubElement(predocOB, "codTipoOB").text = basicos.get("codTipoOB", "OBF")
        ET.SubElement(predocOB, "codCredorDevedor").text = pg["cnpj"]
        ET.SubElement(predocOB, "txtCit").text = pg["txtCit"]

        favo = ET.SubElement(predocOB, "numDomiBancFavo")
        ET.SubElement(favo, "banco").text = pg["banco"]
        ET.SubElement(favo, "agencia").text = pg["agencia"]
        ET.SubElement(favo, "conta").text = basicos.get("contaFavo", "FOPAG")

        pgto = ET.SubElement(predocOB, "numDomiBancPgto")
        ET.SubElement(pgto, "banco").text = basicos.get("bancoPgto", "002")
        ET.SubElement(pgto, "conta").text = basicos.get("contaPgto", "UNICA")

    # Trailler
    t = ET.SubElement(root, ET.QName(SB_NS, "trailler"))
    ET.SubElement(t, ET.QName(SB_NS, "quantidadeDetalhe")).text = "1"

    return root

# =========================
# Streamlit UI
# =========================

st.set_page_config(page_title="Gerador DH001 (DocHabil) - XML", layout="wide")
st.title("Gerador de XML DH001 (DocHabil)")

tabs = st.tabs([
    "1) Header",
    "2) Dados Básicos",
    "3) PCO (colar)",
    "4) Pagamentos (colar)",
    "5) Outros Lanc (colar)",
    "6) Centro de Custo",
    "7) Gerar XML",
])

# Defaults
today = dt.date.today()

with tabs[0]:
    st.subheader("Header (sb:header)")
    col1, col2, col3 = st.columns(3)
    with col1:
        codigoLayout = st.text_input("codigoLayout", value="DH001")
        dataGeracao = st.text_input("dataGeracao (dd/mm/aaaa)", value=today.strftime("%d/%m/%Y"))
    with col2:
        sequencialGeracao = st.text_input("sequencialGeracao (sem zero à esquerda)", value="1")
        anoReferencia = st.text_input("anoReferencia", value=str(today.year))
    with col3:
        ugResponsavel = st.text_input("ugResponsavel", value="120052")
        cpfResponsavel = st.text_input("cpfResponsavel (somente números ou com máscara)", value="09857528740")

with tabs[1]:
    st.subheader("Dados Básicos (dadosBasicos)")
    col1, col2, col3 = st.columns(3)
    with col1:
        codUgEmit = st.text_input("codUgEmit", value="120052")
        anoDH = st.text_input("anoDH", value=str(today.year))
        codTipoDH = st.text_input("codTipoDH", value="FL")
    with col2:
        dtEmis = st.text_input("dtEmis (aaaa-mm-dd)", value=today.strftime("%Y-%m-%d"))
        dtVenc = st.text_input("dtVenc (aaaa-mm-dd)", value=today.strftime("%Y-%m-%d"))
        dtAteste = st.text_input("dtAteste (aaaa-mm-dd)", value=today.strftime("%Y-%m-%d"))
    with col3:
        codUgPgto = st.text_input("codUgPgto", value="120052")
        codCredorDevedor = st.text_input("codCredorDevedor (UG)", value="120052")
        dtPgtoReceb = st.text_input("dtPgtoReceb (aaaa-mm-dd)", value=today.strftime("%Y-%m-%d"))

    txtObser = st.text_input("txtObser", value="PAGAMENTO DA FOPAG JANEIRO/2026 CIVIL")
    txtProcesso = st.text_input("txtProcesso", value="67420.000835/2026-37")

    st.markdown("**Doc Origem (docOrigem)**")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        doc_codIdentEmit = st.text_input("codIdentEmit", value="120052")
    with col2:
        doc_dtEmis = st.text_input("doc.dtEmis (aaaa-mm-dd)", value=today.strftime("%Y-%m-%d"))
    with col3:
        doc_numDocOrigem = st.text_input("numDocOrigem", value="FOPAG.CIVL.JAN")
    with col4:
        st.caption("O vlr do docOrigem será igual ao vlr do dadosBasicos (líquido).")

    st.markdown("**Config pagamentos (predocOB)**")
    col1, col2, col3 = st.columns(3)
    with col1:
        txtObserPredoc = st.text_input("predoc.txtObser", value="PAGAMENTO FOPAG")
        codTipoOB = st.text_input("codTipoOB", value="OBF")
    with col2:
        contaFavo = st.text_input("conta (favorecido)", value="FOPAG")
        bancoPgto = st.text_input("banco (pagador)", value="002")
    with col3:
        contaPgto = st.text_input("conta (pagador)", value="UNICA")

with tabs[2]:
    st.subheader("PCO (colar do Excel)")
    st.caption("Cole linhas com 5 colunas: numEmpe | subitem(2) | codSit | numClassA | valor. Negativos viram despesaAnular automaticamente.")
    pco_text = st.text_area("PCO", height=220, placeholder="Ex:\n2026NE000055\t46\tDFL033\t113110105\t45905,55\n2026NE000055\t46\tAFL033\t113110105\t-31381,26")

with tabs[3]:
    st.subheader("Pagamentos (colar do Excel)")
    st.caption("Cole linhas com 5 colunas: CNPJ | banco | agência | txtCit | valor")
    pgto_text = st.text_area("Pagamentos", height=200, placeholder="00.000.000/0001-91\t001\t1607\t120052FPAG999\t57079618,21")

with tabs[4]:
    st.subheader("Outros Lançamentos (colar do Excel) — OPCIONAL")
    st.caption("Cole 5 colunas: codSit | class3 | class2 | NDD | valor. Se ficar vazio, a tag <outrosLanc> NÃO é gerada.")
    st.caption("Situações possíveis (fixas): PRV001, PRV002, PRV003, LPA385, LPA386 (você pode colar qualquer uma delas).")
    outros_text = st.text_area("Outros Lanc", height=200, placeholder="PRV001\t311110122\t211110101\t31901137\t223,89")

with tabs[5]:
    st.subheader("Centro de Custo (centroCusto)")
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        codCentroCusto = st.text_input("codCentroCusto", value="221A00")
    with col2:
        mesReferencia = st.text_input("mesReferencia (MM)", value="01")
    with col3:
        anoReferenciaCC = st.text_input("anoReferencia", value=str(today.year))
    with col4:
        codUgBenef = st.text_input("codUgBenef", value="120052")
    with col5:
        codSIORG = st.text_input("codSIORG (opcional)", value="2332")

with tabs[6]:
    st.subheader("Gerar XML + Checagens")

    # Parse PCO
    try:
        pco_groups, da_groups, pos_sum, neg_abs_sum, net = parse_pco_block(pco_text)
    except Exception as e:
        st.error(f"Erro ao ler PCO: {e}")
        st.stop()

    # Parse pagamentos
    try:
        pgto_list = parse_pgto_block(pgto_text) if pgto_text.strip() else []
    except Exception as e:
        st.error(f"Erro ao ler Pagamentos: {e}")
        st.stop()

    # Parse outros
    try:
        outros_list = parse_outros_lanc(outros_text)
    except Exception as e:
        st.error(f"Erro ao ler Outros Lanc: {e}")
        st.stop()

    # Compute totals
    total_pgto = sum([p["vlr"] for p in pgto_list], Decimal("0.00"))

    # Centro de custo totals (separados)
    total_cc_base = net  # Σ(relPcoItem) - Σ(relDespesaAnular) = líquido
    total_outros = sum([o["vlr"] for o in outros_list], Decimal("0.00"))

    # Build basicos vlr (líquido) ignoring outrosLanc
    vlr_liquido = net

    st.markdown("### Checagens")
    st.write(f"Total POS (PCO): **{fmt_decimal(pos_sum)}**")
    st.write(f"Total NEG (DespesaAnular, abs): **{fmt_decimal(neg_abs_sum)}**")
    st.write(f"Valor líquido (Dados Básicos): **{fmt_decimal(vlr_liquido)}**")
    st.write(f"Soma Pagamentos (dadosPgto): **{fmt_decimal(total_pgto)}**")
    st.write(f"Soma CentroCusto BASE (relPco - relDespesaAnular): **{fmt_decimal(total_cc_base)}**")
    st.write(f"Total OutrosLanc (separado): **{fmt_decimal(total_outros)}**")

    # Warnings
    if total_pgto != vlr_liquido:
        st.warning("⚠️ Soma de pagamentos NÃO bate com o valor líquido (dadosBasicos).")
    else:
        st.success("✅ Pagamentos batem com o valor líquido (dadosBasicos).")

    if total_cc_base != vlr_liquido:
        st.warning("⚠️ Centro de custo BASE (relPco - relDespesaAnular) NÃO bate com o líquido.")
    else:
        st.success("✅ Centro de custo BASE bate com o líquido.")

    if total_outros > 0:
        st.info("ℹ️ OutrosLanc foi gerado e relacionado no centro de custo via relOutrosLanc (não entra no líquido).")

    # Build XML
    header = {
        "codigoLayout": codigoLayout,
        "dataGeracao": dataGeracao,
        "sequencialGeracao": str(int(sequencialGeracao.strip() or "1")),  # removes leading zeros
        "anoReferencia": anoReferencia,
        "ugResponsavel": ugResponsavel,
        "cpfResponsavel": only_digits(cpfResponsavel),
    }

    basicos = {
        "codUgEmit": codUgEmit,
        "anoDH": anoDH,
        "codTipoDH": codTipoDH,
        "dtEmis": dtEmis,
        "dtVenc": dtVenc,
        "codUgPgto": codUgPgto,
        "vlr": fmt_decimal(vlr_liquido),
        "txtObser": txtObser,
        "txtProcesso": txtProcesso,
        "dtAteste": dtAteste,
        "codCredorDevedor": codCredorDevedor,
        "dtPgtoReceb": dtPgtoReceb,
        "doc_codIdentEmit": doc_codIdentEmit,
        "doc_dtEmis": doc_dtEmis,
        "doc_numDocOrigem": doc_numDocOrigem,
        "doc_vlr": fmt_decimal(vlr_liquido),
        "txtObserPredoc": txtObserPredoc,
        "codTipoOB": codTipoOB,
        "contaFavo": contaFavo,
        "bancoPgto": bancoPgto,
        "contaPgto": contaPgto,
    }

    centro = {
        "codCentroCusto": codCentroCusto,
        "mesReferencia": mesReferencia,
        "anoReferencia": anoReferenciaCC,
        "codUgBenef": codUgBenef,
        "codSIORG": codSIORG.strip() if codSIORG.strip() else "",
    }

    xml_elem = build_xml(
        header=header,
        basicos=basicos,
        pco_groups=pco_groups,
        da_groups=da_groups,
        outros_list=outros_list,
        centro=centro,
        pgto_list=pgto_list,
    )

    xml_str = prettify_xml(xml_elem)

    st.markdown("### Preview (XML)")
    st.code(xml_str, language="xml")

    filename = f"DH001_{basicos['codUgEmit']}_{basicos['dtEmis']}.xml".replace("-", "")
    st.download_button(
        "📥 Baixar XML",
        data=xml_str.encode("utf-8"),
        file_name=filename,
        mime="application/xml",
    )