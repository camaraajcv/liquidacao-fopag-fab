import re
from decimal import Decimal, InvalidOperation
import streamlit as st
import xml.etree.ElementTree as ET
from xml.dom import minidom

st.set_page_config(page_title="Gerador DH001 (SIAFI) - XML", layout="wide")

# ---------------- Helpers ----------------
def parse_decimal_br(s: str) -> Decimal:
    if s is None:
        return Decimal("0")
    s = str(s).strip()
    if not s:
        return Decimal("0")
    # remove milhares e troca decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Valor inválido: {s}")

def fmt_money(d: Decimal) -> str:
    # sempre com 2 casas
    d = d.quantize(Decimal("0.01"))
    return f"{d:.2f}"

def strip_non_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def split_lines_table(text: str):
    lines = []
    for raw in (text or "").splitlines():
        row = raw.strip()
        if not row:
            continue
        # suporta tab, ;, |, múltiplos espaços
        if "\t" in row:
            parts = [p.strip() for p in row.split("\t")]
        elif ";" in row:
            parts = [p.strip() for p in row.split(";")]
        elif "|" in row:
            parts = [p.strip() for p in row.split("|")]
        else:
            parts = re.split(r"\s{2,}|\s+\|\s+|\s+", row.strip())
            parts = [p.strip() for p in parts if p.strip()]
        lines.append(parts)
    return lines

def validate_cols(rows, expected_min):
    ok = []
    bad = []
    for r in rows:
        if len(r) >= expected_min:
            ok.append(r)
        else:
            bad.append(r)
    return ok, bad

def prettify(elem: ET.Element) -> str:
    rough = ET.tostring(elem, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

# ---------------- XML Builder ----------------
def build_xml(cfg: dict) -> str:
    NS_SB = "http://www.tesouro.gov.br/siafi/submissao"
    NS_DH = "http://services.docHabil.cpr.siafi.tesouro.fazenda.gov.br/"
    ET.register_namespace("sb", NS_SB)
    ET.register_namespace("dh", NS_DH)

    sb_arquivo = ET.Element(f"{{{NS_SB}}}arquivo")

    # header
    header = ET.SubElement(sb_arquivo, f"{{{NS_SB}}}header")
    ET.SubElement(header, f"{{{NS_SB}}}codigoLayout").text = "DH001"
    ET.SubElement(header, f"{{{NS_SB}}}dataGeracao").text = cfg["data_geracao"]  # dd/mm/aaaa
    ET.SubElement(header, f"{{{NS_SB}}}sequencialGeracao").text = str(int(cfg["sequencial"]))
    ET.SubElement(header, f"{{{NS_SB}}}anoReferencia").text = str(cfg["ano_ref"])
    ET.SubElement(header, f"{{{NS_SB}}}ugResponsavel").text = str(cfg["ug_resp"])
    ET.SubElement(header, f"{{{NS_SB}}}cpfResponsavel").text = str(cfg["cpf_resp"])

    detalhes = ET.SubElement(sb_arquivo, f"{{{NS_SB}}}detalhes")
    detalhe = ET.SubElement(detalhes, f"{{{NS_SB}}}detalhe")

    cpr = ET.SubElement(detalhe, f"{{{NS_DH}}}CprDhCadastrar")
    ET.SubElement(cpr, "codUgEmit").text = str(cfg["ug_emit"])
    ET.SubElement(cpr, "anoDH").text = str(cfg["ano_ref"])
    ET.SubElement(cpr, "codTipoDH").text = cfg["codTipoDH"]
    # numDH NÃO é opcional no XSD; por isso, aqui nós NÃO colocamos.
    # (Você já conseguiu aceitar recebimento com a estrutura correta que vocês fecharam.)

    # dadosBasicos
    db = ET.SubElement(cpr, "dadosBasicos")
    ET.SubElement(db, "dtEmis").text = cfg["dtEmis"]       # yyyy-mm-dd
    ET.SubElement(db, "dtVenc").text = cfg["dtVenc"]
    ET.SubElement(db, "codUgPgto").text = str(cfg["ug_pgto"])
    ET.SubElement(db, "vlr").text = fmt_money(cfg["vlr_total"])
    ET.SubElement(db, "txtObser").text = cfg["txtObser"]
    ET.SubElement(db, "txtProcesso").text = cfg["txtProcesso"]
    ET.SubElement(db, "dtAteste").text = cfg["dtAteste"]
    ET.SubElement(db, "codCredorDevedor").text = str(cfg["credor_basico"])
    ET.SubElement(db, "dtPgtoReceb").text = cfg["dtPgtoReceb"]

    doc = ET.SubElement(db, "docOrigem")
    ET.SubElement(doc, "codIdentEmit").text = str(cfg["ug_emit"])
    ET.SubElement(doc, "dtEmis").text = cfg["dtEmis"]
    ET.SubElement(doc, "numDocOrigem").text = cfg["numDocOrigem"]
    ET.SubElement(doc, "vlr").text = fmt_money(cfg["vlr_total"])

    # ---- PCO (por codSit agrupado)
    # Estrutura do XSD: pco (numSeqItem, codSit, codUgEmpe, [pcoItem...])
    pco_groups = cfg["pco_groups"]
    for pco_seq, grp in enumerate(pco_groups, start=1):
        pco = ET.SubElement(cpr, "pco")
        ET.SubElement(pco, "numSeqItem").text = str(pco_seq)
        ET.SubElement(pco, "codSit").text = grp["codSit"]
        ET.SubElement(pco, "codUgEmpe").text = str(cfg["ug_emit"])

        for item in grp["items"]:
            pi = ET.SubElement(pco, "pcoItem")
            ET.SubElement(pi, "numSeqItem").text = str(item["numSeqItem"])
            ET.SubElement(pi, "numEmpe").text = item["numEmpe"]
            ET.SubElement(pi, "codSubItemEmpe").text = item["codSubItemEmpe"]
            ET.SubElement(pi, "vlr").text = fmt_money(item["vlr"])
            ET.SubElement(pi, "numClassA").text = item["numClassA"]

    # ---- outrosLanc (opcional)
    if cfg["outros_items"]:
        for it in cfg["outros_items"]:
            ol = ET.SubElement(cpr, "outrosLanc")
            ET.SubElement(ol, "numSeqItem").text = str(it["numSeqItem"])
            ET.SubElement(ol, "codSit").text = it["codSit"]
            ET.SubElement(ol, "vlr").text = fmt_money(it["vlr"])
            # usar numClassA como você pediu (classificação)
            ET.SubElement(ol, "numClassA").text = it["numClassA"]
            # tpNormalEstorno opcional (N/E)
            if it.get("tpNormalEstorno"):
                ET.SubElement(ol, "tpNormalEstorno").text = it["tpNormalEstorno"]

    # ---- despesaAnular (se houver negativos no PCO)
    # Estrutura do XSD: despesaAnular (numSeqItem, codSit, codUgEmpe, [despesaAnularItem...])
    for da_seq, grp in enumerate(cfg["despesa_anular_groups"], start=1):
        da = ET.SubElement(cpr, "despesaAnular")
        ET.SubElement(da, "numSeqItem").text = str(da_seq)
        ET.SubElement(da, "codSit").text = grp["codSit"]
        ET.SubElement(da, "codUgEmpe").text = str(cfg["ug_emit"])

        for item in grp["items"]:
            dai = ET.SubElement(da, "despesaAnularItem")
            ET.SubElement(dai, "numSeqItem").text = str(item["numSeqItem"])
            ET.SubElement(dai, "numEmpe").text = item["numEmpe"]
            ET.SubElement(dai, "codSubItemEmpe").text = item["codSubItemEmpe"]
            ET.SubElement(dai, "vlr").text = fmt_money(item["vlr"])
            ET.SubElement(dai, "numClassA").text = item["numClassA"]

    # ---- centroCusto (1 centro, com relPcoItem + relDespesaAnularItem + relOutrosLanc)
    cc = ET.SubElement(cpr, "centroCusto")
    ET.SubElement(cc, "numSeqItem").text = "1"
    ET.SubElement(cc, "codCentroCusto").text = cfg["codCentroCusto"]
    ET.SubElement(cc, "mesReferencia").text = cfg["mesReferencia"]
    ET.SubElement(cc, "anoReferencia").text = str(cfg["ano_ref"])
    ET.SubElement(cc, "codUgBenef").text = str(cfg["ug_emit"])
    ET.SubElement(cc, "codSIORG").text = cfg["codSIORG"]

    # relPcoItem (aponta para pco_seq e pcoItem_seq)
    for rel in cfg["rel_pco"]:
        r = ET.SubElement(cc, "relPcoItem")
        ET.SubElement(r, "numSeqPai").text = str(rel["numSeqPai"])
        ET.SubElement(r, "numSeqItem").text = str(rel["numSeqItem"])
        ET.SubElement(r, "vlr").text = fmt_money(rel["vlr"])

    # relDespesaAnularItem (aponta para despesaAnular_seq e despesaAnularItem_seq)
    for rel in cfg["rel_despesa_anular"]:
        r = ET.SubElement(cc, "relDespesaAnularItem")
        ET.SubElement(r, "numSeqPai").text = str(rel["numSeqPai"])
        ET.SubElement(r, "numSeqItem").text = str(rel["numSeqItem"])
        ET.SubElement(r, "vlr").text = fmt_money(rel["vlr"])

    # relOutrosLanc (aponta direto para numSeqItem do outrosLanc)
    for rel in cfg["rel_outros"]:
        r = ET.SubElement(cc, "relOutrosLanc")
        ET.SubElement(r, "numSeqItem").text = str(rel["numSeqItem"])
        ET.SubElement(r, "vlr").text = fmt_money(rel["vlr"])

    # ---- dadosPgto (vários blocos)
    for pg in cfg["pagamentos"]:
        dp = ET.SubElement(cpr, "dadosPgto")
        ET.SubElement(dp, "codCredorDevedor").text = pg["cnpj"]
        ET.SubElement(dp, "vlr").text = fmt_money(pg["vlr"])

        predoc = ET.SubElement(dp, "predoc")
        ET.SubElement(predoc, "txtObser").text = cfg["txtObserPgto"]

        ob = ET.SubElement(predoc, "predocOB")
        ET.SubElement(ob, "codTipoOB").text = cfg["codTipoOB"]
        ET.SubElement(ob, "codCredorDevedor").text = pg["cnpj"]
        ET.SubElement(ob, "txtCit").text = pg["txtCit"]

        favo = ET.SubElement(ob, "numDomiBancFavo")
        ET.SubElement(favo, "banco").text = pg["banco"]
        ET.SubElement(favo, "agencia").text = pg["agencia"]
        ET.SubElement(favo, "conta").text = cfg["conta_favo"]

        pgto = ET.SubElement(ob, "numDomiBancPgto")
        ET.SubElement(pgto, "banco").text = cfg["banco_pgto"]
        ET.SubElement(pgto, "conta").text = cfg["conta_pgto"]

    # trailler
    trailler = ET.SubElement(sb_arquivo, f"{{{NS_SB}}}trailler")
    ET.SubElement(trailler, f"{{{NS_SB}}}quantidadeDetalhe").text = "1"

    return prettify(sb_arquivo)

# ---------------- UI ----------------
st.title("Gerador de XML DH001 (SIAFI) - com PCO + Outros Lanc + CentroCusto + Pagamentos")

tabs = st.tabs([
    "1) Cabeçalho",
    "2) Dados Básicos",
    "3) PCO (colar Excel)",
    "4) Outros Lanc (colar Excel)",
    "5) Centro de Custo",
    "6) Pagamentos (colar Excel)",
    "7) Gerar XML"
])

with tabs[0]:
    c1, c2, c3 = st.columns(3)
    with c1:
        data_geracao = st.text_input("sb:dataGeracao (dd/mm/aaaa)", value="28/01/2026")
        sequencial = st.text_input("sb:sequencialGeracao (sem zero à esquerda)", value="1")
    with c2:
        ano_ref = st.number_input("Ano referência", value=2026, step=1)
        ug_resp = st.text_input("UG responsável", value="120052")
    with c3:
        cpf_resp = st.text_input("CPF responsável (somente números ou com pontuação)", value="09857528740")

with tabs[1]:
    c1, c2, c3 = st.columns(3)
    with c1:
        ug_emit = st.text_input("codUgEmit", value="120052")
        ug_pgto = st.text_input("codUgPgto", value="120052")
        credor_basico = st.text_input("codCredorDevedor (dadosBasicos)", value="120052")
    with c2:
        dtEmis = st.text_input("dtEmis (yyyy-mm-dd)", value="2026-01-28")
        dtVenc = st.text_input("dtVenc (yyyy-mm-dd)", value="2026-01-28")
        dtAteste = st.text_input("dtAteste (yyyy-mm-dd)", value="2026-01-28")
    with c3:
        dtPgtoReceb = st.text_input("dtPgtoReceb (yyyy-mm-dd)", value="2026-01-28")
        txtProcesso = st.text_input("txtProcesso", value="67420.000835/2026-37")
        codTipoDH = st.selectbox("codTipoDH", ["FL"], index=0)

    txtObser = st.text_input("txtObser", value="PAGAMENTO DA FOPAG JANEIRO/2026 CIVIL")
    numDocOrigem = st.text_input("numDocOrigem", value="FOPAG.CIVL.JAN")

with tabs[2]:
    st.markdown(
        """
Cole a tabela do PCO (pode vir do Excel) no formato **5 colunas**:

`numEmpe | subItem(2d) | codSit | numClassA | valor`

- Aceita TAB, `;` ou `|`
- Valor pode vir negativo → vai para **despesaAnular** automaticamente.
Exemplo:
`2026NE000055    46    DFL033    113110105    45905,55`
`2026NE000055    46    AFL033    113110105    -31381,26`
"""
    )
    pco_text = st.text_area("PCO (colar aqui)", height=260)

with tabs[3]:
    st.markdown(
        """
Cole Outros Lançamentos no formato **3 ou 4 colunas**:

`codSit | numClassA | valor | (opcional) tpNormalEstorno`

- `tpNormalEstorno`: **N** (normal) ou **E** (estorno)
- Se o valor vier negativo, o app:
  - usa valor positivo (ABS)
  - força `tpNormalEstorno = E`
"""
    )
    outros_text = st.text_area("Outros Lanc (colar aqui)", height=220)

with tabs[4]:
    c1, c2, c3 = st.columns(3)
    with c1:
        codCentroCusto = st.text_input("codCentroCusto", value="221A00")
        mesReferencia = st.text_input("mesReferencia (2 dígitos)", value="01")
    with c2:
        codSIORG = st.text_input("codSIORG", value="2332")
    with c3:
        st.caption("O app vai gerar automaticamente relPcoItem / relDespesaAnularItem / relOutrosLanc")

with tabs[5]:
    st.markdown(
        """
Cole a tabela de pagamentos (OB) no formato **5 colunas**:

`cnpj | banco | agencia | txtCit | valor`

Exemplo:
`00.000.000/0001-91    001    1607    120052FPAG999    57079618,21`
"""
    )
    pgto_text = st.text_area("Pagamentos (colar aqui)", height=220)

    c1, c2, c3 = st.columns(3)
    with c1:
        txtObserPgto = st.text_input("predoc/txtObser", value="PAGAMENTO FOPAG")
        codTipoOB = st.text_input("codTipoOB", value="OBF")
    with c2:
        conta_favo = st.text_input("conta favorecido (fixo)", value="FOPAG")
    with c3:
        banco_pgto = st.text_input("banco pagador (fixo)", value="002")
        conta_pgto = st.text_input("conta pagador (fixo)", value="UNICA")

with tabs[6]:
    st.subheader("Gerar XML")

    # ---- Parse PCO
    pco_rows_all = split_lines_table(pco_text)
    pco_rows, pco_bad = validate_cols(pco_rows_all, 5)

    # ---- Parse Outros
    outros_rows_all = split_lines_table(outros_text)
    # aceita 3 ou 4 cols
    outros_ok = []
    outros_bad = []
    for r in outros_rows_all:
        if len(r) >= 3:
            outros_ok.append(r)
        else:
            outros_bad.append(r)

    # ---- Parse Pagamentos
    pgto_rows_all = split_lines_table(pgto_text)
    pgto_rows, pgto_bad = validate_cols(pgto_rows_all, 5)

    if pco_bad:
        st.warning(f"Linhas PCO ignoradas (colunas insuficientes): {pco_bad[:5]}")

    if outros_bad:
        st.warning(f"Linhas OutrosLanc ignoradas (colunas insuficientes): {outros_bad[:5]}")

    if pgto_bad:
        st.warning(f"Linhas Pagamentos ignoradas (colunas insuficientes): {pgto_bad[:5]}")

    # ---- Transform PCO into groups (positivos por codSit; negativos por codSit)
    # Cada grupo de pco tem numSeqItem "pai"; dentro, pcoItem tem sequencial próprio.
    pos_by_sit = {}
    neg_by_sit = {}

    # Guardar referências para gerar relPcoItem/relDespesaAnularItem
    # (numSeqPai, numSeqItem, vlr)
    # numSeqPai = sequência do grupo (na ordem que vamos montar)
    for r in pco_rows:
        numEmpe, subitem, codSit_row, numClassA, vlr_raw = r[:5]
        vlr = parse_decimal_br(vlr_raw)

        rec = dict(
            numEmpe=numEmpe.strip(),
            codSubItemEmpe=str(subitem).strip().zfill(2),
            codSit=codSit_row.strip(),
            numClassA=str(numClassA).strip(),
            vlr=abs(vlr)
        )
        if vlr >= 0 and parse_decimal_br(vlr_raw) >= 0:
            pos_by_sit.setdefault(rec["codSit"], []).append(rec)
        else:
            # negativo -> despesaAnular
            neg_by_sit.setdefault(rec["codSit"], []).append(rec)

    # Para manter consistente, ordena codSit
    pco_groups = []
    rel_pco = []
    for sit in sorted(pos_by_sit.keys()):
        items = pos_by_sit[sit]
        # numSeqItem dentro do grupo
        for idx, it in enumerate(items, start=1):
            it["numSeqItem"] = idx
        pco_groups.append({"codSit": sit, "items": items})

    # Monta despesaAnular groups
    despesa_anular_groups = []
    rel_despesa_anular = []
    for sit in sorted(neg_by_sit.keys()):
        items = neg_by_sit[sit]
        for idx, it in enumerate(items, start=1):
            it["numSeqItem"] = idx
        despesa_anular_groups.append({"codSit": sit, "items": items})

    # ---- Outros Lanc items + relOutrosLanc
    outros_items = []
    rel_outros = []
    for idx, r in enumerate(outros_ok, start=1):
        codSit_ol = r[0].strip()
        numClassA_ol = r[1].strip()
        vlr = parse_decimal_br(r[2])
        tp = (r[3].strip().upper() if len(r) >= 4 and r[3].strip() else "")

        # se veio negativo, vira ABS e vira estorno
        if vlr < 0:
            vlr = abs(vlr)
            tp = "E"

        if tp and tp not in ("N", "E"):
            st.warning(f"tpNormalEstorno inválido na linha {idx} (use N/E). Ignorando tp.")
            tp = ""

        outros_items.append({
            "numSeqItem": idx,
            "codSit": codSit_ol,
            "numClassA": numClassA_ol,
            "vlr": vlr,
            "tpNormalEstorno": tp
        })
        rel_outros.append({"numSeqItem": idx, "vlr": vlr})

    # ---- Relacionamentos CentroCusto
    # relPcoItem usa numSeqPai = índice do grupo pco, numSeqItem = item seq
    for pai, grp in enumerate(pco_groups, start=1):
        for it in grp["items"]:
            rel_pco.append({"numSeqPai": pai, "numSeqItem": it["numSeqItem"], "vlr": it["vlr"]})

    # relDespesaAnularItem usa numSeqPai = índice do grupo despesaAnular, numSeqItem = item seq
    for pai, grp in enumerate(despesa_anular_groups, start=1):
        for it in grp["items"]:
            rel_despesa_anular.append({"numSeqPai": pai, "numSeqItem": it["numSeqItem"], "vlr": it["vlr"]})

    # ---- Total líquido (vlr dadosBasicos)
    # líquido = soma(POS) - soma(NEG)
    total_pos = sum([it["vlr"] for g in pco_groups for it in g["items"]], Decimal("0"))
    total_neg = sum([it["vlr"] for g in despesa_anular_groups for it in g["items"]], Decimal("0"))
    total_outros = sum([it["vlr"] for it in outros_items], Decimal("0"))
    vlr_liquido = (total_pos + total_outros - total_neg)

    # ---- Pagamentos
    pagamentos = []
    total_pgto = Decimal("0")
    for r in pgto_rows:
        cnpj, banco, agencia, txtCit_row, vlr_raw = r[:5]
        vlr = parse_decimal_br(vlr_raw)
        pagamentos.append({
            "cnpj": strip_non_digits(cnpj),
            "banco": banco.strip().zfill(3),
            "agencia": agencia.strip(),
            "txtCit": txtCit_row.strip(),
            "vlr": vlr
        })
        total_pgto += vlr

    # ---- Checagens de consistência
    total_cc = sum([x["vlr"] for x in rel_pco], Decimal("0")) \
               + sum([x["vlr"] for x in rel_outros], Decimal("0")) \
               - sum([x["vlr"] for x in rel_despesa_anular], Decimal("0"))

    st.write("### Checagens")
    st.write(f"Total POS (PCO): **{fmt_money(total_pos)}**")
    st.write(f"Total NEG (DespesaAnular): **{fmt_money(total_neg)}**")
    st.write(f"Valor líquido (Dados Básicos): **{fmt_money(vlr_liquido)}**")
    st.write(f"Soma Pagamentos (dadosPgto): **{fmt_money(total_pgto)}**")
    st.write(f"Soma CentroCusto (relPco + relOutros - relDespesaAnular): **{fmt_money(total_cc)}**")
    st.write(f"Total OutrosLanc: **{fmt_money(total_outros)}**")
    if vlr_liquido <= 0:
        st.error("⚠️ Valor líquido <= 0. Verifique os itens.")
    if total_pgto != vlr_liquido:
        st.warning("⚠️ Soma de pagamentos NÃO bate com o valor líquido (dadosBasicos).")
    if total_cc != vlr_liquido:
        st.warning("⚠️ Soma do Centro de Custo NÃO bate com o valor líquido (dadosBasicos).")

    # ---- Botão gerar
    if st.button("✅ Gerar XML"):
        cfg = dict(
            data_geracao=data_geracao.strip(),
            sequencial=sequencial.strip(),
            ano_ref=int(ano_ref),
            ug_resp=ug_resp.strip(),
            cpf_resp=strip_non_digits(cpf_resp),

            ug_emit=ug_emit.strip(),
            ug_pgto=ug_pgto.strip(),
            credor_basico=credor_basico.strip(),
            codTipoDH=codTipoDH.strip(),

            dtEmis=dtEmis.strip(),
            dtVenc=dtVenc.strip(),
            dtAteste=dtAteste.strip(),
            dtPgtoReceb=dtPgtoReceb.strip(),
            txtProcesso=txtProcesso.strip(),
            txtObser=txtObser.strip(),
            numDocOrigem=numDocOrigem.strip(),

            vlr_total=vlr_liquido,

            pco_groups=pco_groups,
            despesa_anular_groups=despesa_anular_groups,

            outros_items=outros_items,

            codCentroCusto=codCentroCusto.strip(),
            mesReferencia=mesReferencia.strip().zfill(2),
            codSIORG=codSIORG.strip(),

            rel_pco=rel_pco,
            rel_despesa_anular=rel_despesa_anular,
            rel_outros=rel_outros,

            pagamentos=pagamentos,
            txtObserPgto=txtObserPgto.strip(),
            codTipoOB=codTipoOB.strip(),
            conta_favo=conta_favo.strip(),
            banco_pgto=banco_pgto.strip().zfill(3),
            conta_pgto=conta_pgto.strip(),
        )

        xml_str = build_xml(cfg)
        st.success("XML gerado!")
        st.download_button(
            "⬇️ Baixar XML",
            data=xml_str.encode("utf-8"),
            file_name="DH001_GERADO.xml",
            mime="application/xml"
        )
        st.text_area("Prévia do XML", xml_str, height=380)