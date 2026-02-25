import re
import io
import datetime as dt
import streamlit as st
import xml.etree.ElementTree as ET

# =========================
# Helpers (parsing / format)
# =========================

def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def parse_money_br(x: str) -> float:
    """
    Aceita:
      45905,55
      45.905,55
      45905.55
      -31.381,26
    Retorna float.
    """
    s = (x or "").strip()
    if not s:
        return 0.0
    # remove espaços
    s = s.replace(" ", "")
    # se tem vírgula, assume padrão BR: milhares "." e decimal ","
    if "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    return float(s)

def fmt_money_dot(v: float) -> str:
    # sempre com ponto e 2 casas
    return f"{v:.2f}"

def parse_paste_table(text: str, expected_cols: int):
    """
    Lê uma tabela colada do Excel (TSV ou espaços múltiplos).
    Ignora linhas vazias.
    """
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # tenta tab primeiro
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        else:
            # separa por múltiplos espaços
            parts = [p.strip() for p in re.split(r"\s{2,}|\s+\|\s+|\s+", line) if p.strip()]

        # se veio "colado" com separador único e sobrou menos, tenta split por ";"
        if len(parts) < expected_cols and ";" in line:
            parts = [p.strip() for p in line.split(";")]

        if len(parts) < expected_cols:
            # linha incompleta: ignora, mas mostra depois
            rows.append(("__INVALID__", parts, raw))
            continue

        rows.append(("__OK__", parts[:expected_cols], raw))
    return rows

# =========================
# XML builders
# =========================

SB_NS = "http://www.tesouro.gov.br/siafi/submissao"
DH_NS = "http://services.docHabil.cpr.siafi.tesouro.fazenda.gov.br/"

ET.register_namespace("sb", SB_NS)
ET.register_namespace("dh", DH_NS)

def sb(tag):  # sb namespace tag
    return f"{{{SB_NS}}}{tag}"

def dh(tag):  # dh namespace tag
    return f"{{{DH_NS}}}{tag}"

def add_text(parent, tag, text):
    el = ET.SubElement(parent, tag)
    el.text = str(text)
    return el

def build_xml(payload: dict) -> bytes:
    """
    payload contém:
      header, dadosBasicos, pco_items, outros_items, pgto_items, centroCusto_cfg
    """
    # Root
    root = ET.Element(sb("arquivo"))

    # header
    header = ET.SubElement(root, sb("header"))
    add_text(header, sb("codigoLayout"), payload["header"]["codigoLayout"])
    add_text(header, sb("dataGeracao"), payload["header"]["dataGeracao"])
    add_text(header, sb("sequencialGeracao"), payload["header"]["sequencialGeracao"])
    add_text(header, sb("anoReferencia"), payload["header"]["anoReferencia"])
    add_text(header, sb("ugResponsavel"), payload["header"]["ugResponsavel"])
    add_text(header, sb("cpfResponsavel"), payload["header"]["cpfResponsavel"])

    detalhes = ET.SubElement(root, sb("detalhes"))
    detalhe = ET.SubElement(detalhes, sb("detalhe"))

    cadastrar = ET.SubElement(detalhe, dh("CprDhCadastrar"))

    # Campos fixos topo
    add_text(cadastrar, "codUgEmit", payload["topo"]["codUgEmit"])
    add_text(cadastrar, "anoDH", payload["topo"]["anoDH"])
    add_text(cadastrar, "codTipoDH", payload["topo"]["codTipoDH"])
    # numDH: NÃO inserir aqui (deu erro quando vazio; e você pediu para omitir)

    # dadosBasicos
    db = ET.SubElement(cadastrar, "dadosBasicos")
    add_text(db, "dtEmis", payload["dadosBasicos"]["dtEmis"])
    add_text(db, "dtVenc", payload["dadosBasicos"]["dtVenc"])
    add_text(db, "codUgPgto", payload["dadosBasicos"]["codUgPgto"])
    add_text(db, "vlr", payload["dadosBasicos"]["vlr"])
    add_text(db, "txtObser", payload["dadosBasicos"]["txtObser"])
    add_text(db, "txtProcesso", payload["dadosBasicos"]["txtProcesso"])
    add_text(db, "dtAteste", payload["dadosBasicos"]["dtAteste"])
    add_text(db, "codCredorDevedor", payload["dadosBasicos"]["codCredorDevedor"])
    add_text(db, "dtPgtoReceb", payload["dadosBasicos"]["dtPgtoReceb"])

    doc = ET.SubElement(db, "docOrigem")
    add_text(doc, "codIdentEmit", payload["docOrigem"]["codIdentEmit"])
    add_text(doc, "dtEmis", payload["docOrigem"]["dtEmis"])
    add_text(doc, "numDocOrigem", payload["docOrigem"]["numDocOrigem"])
    add_text(doc, "vlr", payload["docOrigem"]["vlr"])

    # =========================
    # PCO (positivos) agrupado por codSit
    # =========================
    # XSD aceita vários <pco>. Cada pco tem:
    #   numSeqItem, codSit, codUgEmpe, (pcoItem 1..n)
    # pcoItem sequência (usando o que passou no validador):
    #   numSeqItem, numEmpe, codSubItemEmpe, vlr, numClassA
    pco_groups = payload["pco_groups"]  # list of dicts

    for g in pco_groups:
        pco = ET.SubElement(cadastrar, "pco")
        add_text(pco, "numSeqItem", g["numSeqItem"])
        add_text(pco, "codSit", g["codSit"])
        add_text(pco, "codUgEmpe", g["codUgEmpe"])
        for item in g["items"]:
            it = ET.SubElement(pco, "pcoItem")
            add_text(it, "numSeqItem", item["numSeqItem"])
            add_text(it, "numEmpe", item["numEmpe"])
            add_text(it, "codSubItemEmpe", item["codSubItemEmpe"])
            add_text(it, "vlr", item["vlr"])
            add_text(it, "numClassA", item["numClassA"])

    # =========================
    # OUTROS LANCAMENTOS (nome correto: outrosLanc)
    # ORDEM CORRETA: depois de pco e ANTES de despesaAnular/centroCusto/dadosPgto
    # =========================
    # outrosLanc sequência mínima:
    #   numSeqItem, codSit, (opcionais...), vlr, (opcionais numClassA..D etc.)
    outros = payload["outros_items"]  # list
    for o in outros:
        ol = ET.SubElement(cadastrar, "outrosLanc")
        add_text(ol, "numSeqItem", o["numSeqItem"])
        add_text(ol, "codSit", o["codSit"])
        add_text(ol, "vlr", o["vlr"])
        # classificações (se houver)
        if o.get("numClassA"):
            add_text(ol, "numClassA", o["numClassA"])  # classe 3
        if o.get("numClassD"):
            add_text(ol, "numClassD", o["numClassD"])  # classe 2

    # =========================
    # DESPESA ANULAR (automático a partir de negativos no PCO)
    # XSD: despesaAnular contém: numSeqItem, codSit, codUgEmpe, despesaAnularItem(1..n)
    # despesaAnularItem: numSeqItem, numEmpe, codSubItemEmpe, vlr, numClassA
    # =========================
    for d in payload["despesa_anular_groups"]:
        da = ET.SubElement(cadastrar, "despesaAnular")
        add_text(da, "numSeqItem", d["numSeqItem"])
        add_text(da, "codSit", d["codSit"])
        add_text(da, "codUgEmpe", d["codUgEmpe"])
        for item in d["items"]:
            di = ET.SubElement(da, "despesaAnularItem")
            add_text(di, "numSeqItem", item["numSeqItem"])
            add_text(di, "numEmpe", item["numEmpe"])
            add_text(di, "codSubItemEmpe", item["codSubItemEmpe"])
            add_text(di, "vlr", item["vlr"])           # já vem positivo
            add_text(di, "numClassA", item["numClassA"])

    # =========================
    # CENTRO DE CUSTO
    # ordem interna relevante:
    #   relPcoItem (0..n)
    #   relOutrosLanc (0..n)   <-- nome correto e vem antes de relDespesaAnular
    #   relDespesaAnular (0..n)
    # =========================
    cc_cfg = payload["centroCusto_cfg"]
    cc = ET.SubElement(cadastrar, "centroCusto")
    add_text(cc, "numSeqItem", cc_cfg["numSeqItem"])
    add_text(cc, "codCentroCusto", cc_cfg["codCentroCusto"])
    add_text(cc, "mesReferencia", cc_cfg["mesReferencia"])
    add_text(cc, "anoReferencia", cc_cfg["anoReferencia"])
    add_text(cc, "codUgBenef", cc_cfg["codUgBenef"])
    add_text(cc, "codSIORG", cc_cfg["codSIORG"])

    # relPcoItem
    for r in payload["rel_pco_items"]:
        rp = ET.SubElement(cc, "relPcoItem")
        add_text(rp, "numSeqPai", r["numSeqPai"])
        add_text(rp, "numSeqItem", r["numSeqItem"])
        add_text(rp, "vlr", r["vlr"])

    # relOutrosLanc (se houver)
    for r in payload["rel_outros_items"]:
        ro = ET.SubElement(cc, "relOutrosLanc")
        add_text(ro, "numSeqItem", r["numSeqItem"])
        if r.get("codNatDespDet"):
            add_text(ro, "codNatDespDet", r["codNatDespDet"])  # NDD sem separador
        add_text(ro, "vlr", r["vlr"])

    # relDespesaAnular (se houver)
    for r in payload["rel_despesa_anular_items"]:
        rd = ET.SubElement(cc, "relDespesaAnular")
        add_text(rd, "numSeqPai", r["numSeqPai"])
        add_text(rd, "numSeqItem", r["numSeqItem"])
        add_text(rd, "vlr", r["vlr"])

    # =========================
    # DADOS PGTO (vários)
    # =========================
    for p in payload["pgto_items"]:
        dp = ET.SubElement(cadastrar, "dadosPgto")
        add_text(dp, "codCredorDevedor", p["codCredorDevedor"])
        add_text(dp, "vlr", p["vlr"])
        predoc = ET.SubElement(dp, "predoc")
        add_text(predoc, "txtObser", p["txtObser"])
        pob = ET.SubElement(predoc, "predocOB")
        add_text(pob, "codTipoOB", p["codTipoOB"])
        add_text(pob, "codCredorDevedor", p["codCredorDevedor"])
        add_text(pob, "txtCit", p["txtCit"])

        favo = ET.SubElement(pob, "numDomiBancFavo")
        add_text(favo, "banco", p["bancoFavo"])
        add_text(favo, "agencia", p["agenciaFavo"])
        add_text(favo, "conta", p["contaFavo"])

        pg = ET.SubElement(pob, "numDomiBancPgto")
        add_text(pg, "banco", p["bancoPgto"])
        add_text(pg, "conta", p["contaPgto"])

    # trailler
    tr = ET.SubElement(root, sb("trailler"))
    add_text(tr, sb("quantidadeDetalhe"), "1")

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_bytes


# =========================
# Streamlit UI
# =========================

st.set_page_config(page_title="Gerador DH001 (FOPAG)", layout="wide")
st.title("Gerador de XML DH001 (SIAFI) — FOPAG")

def ti(label, value="", prefix=""):
    return st.text_input(label, value=value, key=f"{prefix}:{label}")

def ta(label, value="", prefix="", height=160):
    return st.text_area(label, value=value, height=height, key=f"{prefix}:{label}")

today = dt.date.today()

tab_basicos, tab_pco, tab_outros, tab_cc, tab_pgto, tab_gerar = st.tabs(
    ["Dados Básicos", "PCO (colar)", "Outros Lançamentos (colar)", "Centro de Custo", "Pagamentos (colar)", "Gerar XML"]
)

# -------------------------
# Dados Básicos
# -------------------------
with tab_basicos:
    prefix = "basicos"
    col1, col2, col3 = st.columns(3)

    with col1:
        codUgEmit = ti("codUgEmit", "120052", prefix)
        anoDH = ti("anoDH", str(today.year), prefix)
        codTipoDH = ti("codTipoDH", "FL", prefix)

    with col2:
        dtEmis = ti("dtEmis (AAAA-MM-DD)", str(today), prefix)
        dtVenc = ti("dtVenc (AAAA-MM-DD)", str(today), prefix)
        dtAteste = ti("dtAteste (AAAA-MM-DD)", str(today), prefix)

    with col3:
        codUgPgto = ti("codUgPgto", "120052", prefix)
        codCredorDevedor = ti("codCredorDevedor (UG)", "120052", prefix)
        dtPgtoReceb = ti("dtPgtoReceb (AAAA-MM-DD)", str(today), prefix)

    st.divider()
    col4, col5 = st.columns(2)
    with col4:
        txtObser = ti("txtObser", "PAGAMENTO DA FOPAG JANEIRO/2026 CIVIL", prefix)
        txtProcesso = ti("txtProcesso", "67420.000835/2026-37", prefix)
    with col5:
        numDocOrigem = ti("numDocOrigem", "FOPAG.CIVL.JAN", prefix)
        codIdentEmit = ti("codIdentEmit", "120052", prefix)

    st.info("⚠️ O valor líquido (dadosBasicos.vlr) será calculado automaticamente a partir do PCO (positivos - negativos).")

# -------------------------
# PCO (colar)
# -------------------------
with tab_pco:
    prefix = "pco"
    st.markdown("Cole linhas no formato (5 colunas): **numEmpe | subitem(2) | codSit | numClassA | valor**")
    st.caption("Ex.: 2026NE000055    46    DFL033    113110105    45905,55")
    pco_text = ta("PCO (colar)", "", prefix, height=220)
    codUgEmpe = ti("codUgEmpe (para PCO/DespesaAnular)", "120052", prefix)

# -------------------------
# Outros Lançamentos (colar)
# -------------------------
with tab_outros:
    prefix = "outros"
    st.markdown("Cole linhas no formato (5 colunas): **codSit | classe3 | classe2 | NDD | valor**")
    st.caption("Ex.: PRV001    311110101    211110101    31901137    223,89")
    st.caption("Situações comuns: PRV001, PRV002, PRV003, LPA385, LPA386")
    outros_text = ta("Outros Lançamentos (colar) — opcional", "", prefix, height=220)

# -------------------------
# Centro de Custo
# -------------------------
with tab_cc:
    prefix = "cc"
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        codCentroCusto = ti("codCentroCusto", "221A00", prefix)
    with col2:
        mesReferencia = ti("mesReferencia (MM)", "01", prefix)
    with col3:
        anoReferenciaCC = ti("anoReferencia", str(today.year), prefix)
    with col4:
        codUgBenef = ti("codUgBenef", "120052", prefix)
    with col5:
        codSIORG = ti("codSIORG", "2332", prefix)

# -------------------------
# Pagamentos (colar)
# -------------------------
with tab_pgto:
    prefix = "pgto"
    st.markdown("Cole linhas no formato (5 colunas): **CNPJ | banco | agencia | txtCit | valor**")
    st.caption("Ex.: 00.000.000/0001-91    001    1607    120052FPAG999    57.079.618,21")
    pgto_text = ta("Pagamentos (colar)", "", prefix, height=220)

    colA, colB, colC = st.columns(3)
    with colA:
        codTipoOB = ti("codTipoOB", "OBF", prefix)
    with colB:
        contaFavo = ti("conta FAVO", "FOPAG", prefix)
    with colC:
        bancoPgto = ti("banco PGTO", "002", prefix)
        contaPgto = ti("conta PGTO", "UNICA", prefix)

# -------------------------
# Gerar / Checagens
# -------------------------
with tab_gerar:
    prefix = "gerar"

    # Header info
    st.subheader("Header do arquivo")
    col1, col2, col3 = st.columns(3)
    with col1:
        codigoLayout = ti("codigoLayout", "DH001", prefix)
        ugResponsavel = ti("ugResponsavel", "120052", prefix)
    with col2:
        dataGeracao = ti("dataGeracao (DD/MM/AAAA)", today.strftime("%d/%m/%Y"), prefix)
        sequencialGeracao = ti("sequencialGeracao (sem zero à esquerda)", "1", prefix)
    with col3:
        anoReferenciaHeader = ti("anoReferencia (header)", str(today.year), prefix)
        cpfResponsavel = ti("cpfResponsavel", "09857528740", prefix)

    # =========
    # Parse PCO
    # =========
    pco_rows = parse_paste_table(pco_text, expected_cols=5)
    invalid_pco = [r for r in pco_rows if r[0] == "__INVALID__"]
    ok_pco = [r for r in pco_rows if r[0] == "__OK__"]

    pco_lines = []
    neg_lines = []
    for _, parts, raw in ok_pco:
        numEmpe, subitem, sit, classA, val = parts
        v = parse_money_br(val)
        item = {
            "numEmpe": numEmpe.strip(),
            "codSubItemEmpe": subitem.strip().zfill(2),
            "codSit": sit.strip(),
            "numClassA": only_digits(classA.strip()),
            "vlr_float": v,
            "raw": raw
        }
        if v < 0:
            neg_lines.append(item)
        else:
            pco_lines.append(item)

    total_pos = sum(x["vlr_float"] for x in pco_lines)
    total_neg = sum(abs(x["vlr_float"]) for x in neg_lines)
    liquido = total_pos - total_neg

    # =========
    # Parse Outros
    # =========
    outros_rows = parse_paste_table(outros_text, expected_cols=5) if (outros_text or "").strip() else []
    invalid_outros = [r for r in outros_rows if r[0] == "__INVALID__"]
    ok_outros = [r for r in outros_rows if r[0] == "__OK__"]

    outros_items = []
    total_outros = 0.0
    for idx, (_, parts, raw) in enumerate(ok_outros, start=1):
        sit, class3, class2, ndd, val = parts
        v = parse_money_br(val)
        total_outros += v
        outros_items.append({
            "numSeqItem": str(idx),
            "codSit": sit.strip().upper(),
            "numClassA": only_digits(class3),  # classe 3
            "numClassD": only_digits(class2),  # classe 2
            "codNatDespDet": only_digits(ndd), # NDD p/ centro de custo
            "vlr": fmt_money_dot(v),
            "vlr_float": v,
            "raw": raw
        })

    # =========
    # Parse Pagamentos
    # =========
    pgto_rows = parse_paste_table(pgto_text, expected_cols=5)
    invalid_pgto = [r for r in pgto_rows if r[0] == "__INVALID__"]
    ok_pgto = [r for r in pgto_rows if r[0] == "__OK__"]

    pgto_items = []
    total_pgto = 0.0
    for _, parts, raw in ok_pgto:
        cnpj, banco, agencia, txtcit, val = parts
        v = parse_money_br(val)
        total_pgto += v
        pgto_items.append({
            "codCredorDevedor": only_digits(cnpj),
            "vlr": fmt_money_dot(v),
            "vlr_float": v,
            "txtObser": "PAGAMENTO FOPAG",
            "codTipoOB": codTipoOB.strip(),
            "txtCit": txtcit.strip(),
            "bancoFavo": banco.strip().zfill(3),
            "agenciaFavo": agencia.strip().zfill(4),
            "contaFavo": contaFavo.strip(),
            "bancoPgto": bancoPgto.strip().zfill(3),
            "contaPgto": contaPgto.strip(),
            "raw": raw
        })

    # =========
    # Build PCO groups (positivos) por codSit
    # =========
    pco_by_sit = {}
    for item in pco_lines:
        pco_by_sit.setdefault(item["codSit"], []).append(item)

    pco_groups = []
    rel_pco_items = []
    seq_pco = 0
    for sit, items in pco_by_sit.items():
        seq_pco += 1
        group_seq = str(seq_pco)
        group_items = []
        for i, it in enumerate(items, start=1):
            group_items.append({
                "numSeqItem": str(i),
                "numEmpe": it["numEmpe"],
                "codSubItemEmpe": it["codSubItemEmpe"],
                "vlr": fmt_money_dot(it["vlr_float"]),
                "numClassA": it["numClassA"],
            })
            rel_pco_items.append({
                "numSeqPai": group_seq,
                "numSeqItem": str(i),
                "vlr": fmt_money_dot(it["vlr_float"])
            })
        pco_groups.append({
            "numSeqItem": group_seq,
            "codSit": sit,
            "codUgEmpe": codUgEmpe.strip(),
            "items": group_items
        })

    # =========
    # Build DespesaAnular groups (negativos) por codSit
    # =========
    neg_by_sit = {}
    for item in neg_lines:
        neg_by_sit.setdefault(item["codSit"], []).append(item)

    despesa_anular_groups = []
    rel_despesa_anular_items = []
    seq_da = 0
    for sit, items in neg_by_sit.items():
        seq_da += 1
        group_seq = str(seq_da)
        group_items = []
        for i, it in enumerate(items, start=1):
            v_abs = abs(it["vlr_float"])
            group_items.append({
                "numSeqItem": str(i),
                "numEmpe": it["numEmpe"],
                "codSubItemEmpe": it["codSubItemEmpe"],
                "vlr": fmt_money_dot(v_abs),     # SEM sinal
                "numClassA": it["numClassA"]
            })
            rel_despesa_anular_items.append({
                "numSeqPai": group_seq,
                "numSeqItem": str(i),
                "vlr": fmt_money_dot(v_abs)
            })
        despesa_anular_groups.append({
            "numSeqItem": group_seq,
            "codSit": sit,
            "codUgEmpe": codUgEmpe.strip(),
            "items": group_items
        })

    # =========
    # Centro de custo: relOutrosLanc
    # =========
    rel_outros_items = []
    for o in outros_items:
        rel_outros_items.append({
            "numSeqItem": o["numSeqItem"],
            "codNatDespDet": o["codNatDespDet"],
            "vlr": o["vlr"]
        })

    # =========
    # Dados básicos (vlr = líquido)
    # =========
    dadosBasicosVlr = fmt_money_dot(liquido)

    # docOrigem vlr acompanha dadosBasicos vlr
    docOrigemVlr = dadosBasicosVlr

    # =========
    # Checagens
    # =========
    st.subheader("Checagens")

    st.write(f"Total POS (PCO): **{fmt_money_dot(total_pos)}**")
    st.write(f"Total NEG (DespesaAnular): **{fmt_money_dot(total_neg)}**")
    st.write(f"Valor líquido (Dados Básicos): **{dadosBasicosVlr}**")
    st.write(f"Total OutrosLanc: **{fmt_money_dot(total_outros)}** (não entra no líquido)")
    st.write(f"Soma Pagamentos (dadosPgto): **{fmt_money_dot(total_pgto)}**")

    # Centro de custo (como você vinha usando): relPco + relOutros - relDespesaAnular
    soma_cc = (total_pos + total_outros - total_neg)
    st.write(f"Soma CentroCusto (relPco + relOutros - relDespesaAnular): **{fmt_money_dot(soma_cc)}**")

    if invalid_pco:
        st.warning(f"PCO: {len(invalid_pco)} linha(s) com colunas insuficientes (foram ignoradas).")
    if invalid_outros:
        st.warning(f"OutrosLanc: {len(invalid_outros)} linha(s) inválida(s) (foram ignoradas).")
    if invalid_pgto:
        st.warning(f"Pagamentos: {len(invalid_pgto)} linha(s) inválida(s) (foram ignoradas).")

    # Regra de consistência principal (SIAFI): soma pagamentos deve bater com o líquido
    if abs(total_pgto - liquido) > 0.005:
        st.error("⚠️ Soma de pagamentos NÃO bate com o valor líquido (dadosBasicos).")
    else:
        st.success("✅ Soma de pagamentos bate com o valor líquido (dadosBasicos).")

    # =========
    # Build payload e gerar XML
    # =========
    payload = {
        "header": {
            "codigoLayout": codigoLayout.strip(),
            "dataGeracao": dataGeracao.strip(),
            "sequencialGeracao": str(int(sequencialGeracao.strip() or "1")),  # remove zero à esquerda
            "anoReferencia": anoReferenciaHeader.strip(),
            "ugResponsavel": ugResponsavel.strip(),
            "cpfResponsavel": only_digits(cpfResponsavel),
        },
        "topo": {
            "codUgEmit": codUgEmit.strip(),
            "anoDH": anoDH.strip(),
            "codTipoDH": codTipoDH.strip(),
        },
        "dadosBasicos": {
            "dtEmis": dtEmis.strip(),
            "dtVenc": dtVenc.strip(),
            "codUgPgto": codUgPgto.strip(),
            "vlr": dadosBasicosVlr,
            "txtObser": txtObser.strip(),
            "txtProcesso": txtProcesso.strip(),
            "dtAteste": dtAteste.strip(),
            "codCredorDevedor": codCredorDevedor.strip(),
            "dtPgtoReceb": dtPgtoReceb.strip(),
        },
        "docOrigem": {
            "codIdentEmit": codIdentEmit.strip(),
            "dtEmis": dtEmis.strip(),
            "numDocOrigem": numDocOrigem.strip(),
            "vlr": docOrigemVlr,
        },
        "pco_groups": pco_groups,
        # outrosLanc é opcional: se vazio, lista vazia => não gera tag
        "outros_items": [
            {
                "numSeqItem": o["numSeqItem"],
                "codSit": o["codSit"],
                "numClassA": o["numClassA"],
                "numClassD": o["numClassD"],
                "vlr": o["vlr"]
            } for o in outros_items
        ],
        "despesa_anular_groups": despesa_anular_groups,
        "centroCusto_cfg": {
            "numSeqItem": "1",
            "codCentroCusto": codCentroCusto.strip(),
            "mesReferencia": mesReferencia.strip().zfill(2),
            "anoReferencia": anoReferenciaCC.strip(),
            "codUgBenef": codUgBenef.strip(),
            "codSIORG": codSIORG.strip(),
        },
        "rel_pco_items": rel_pco_items,
        "rel_outros_items": rel_outros_items,
        "rel_despesa_anular_items": rel_despesa_anular_items,
        "pgto_items": pgto_items,
    }

    xml_bytes = build_xml(payload)

    st.download_button(
        "⬇️ Baixar XML (DH001)",
        data=xml_bytes,
        file_name="DH001_FOPAG.xml",
        mime="application/xml",
        key="download_xml_btn"
    )

    st.caption("Dica: se o SIAFI rejeitar algo, cole aqui o ERxxxx e o trecho do XML que eu ajusto a regra no gerador.")