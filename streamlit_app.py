import re
import xml.etree.ElementTree as ET
from datetime import date
import streamlit as st

# -------------------------
# Helpers
# -------------------------

def norm_decimal_br_to_dot(s: str) -> float:
    """
    Aceita: 1.234.567,89 | 1234,56 | 1234.56
    Retorna float com ponto.
    """
    if s is None:
        return 0.0
    s = str(s).strip()
    if not s:
        return 0.0
    # remove espaços
    s = s.replace(" ", "")
    # se tem vírgula como decimal, remove milhares (pontos) e troca vírgula por ponto
    if "," in s and "." in s:
        # assume padrão BR
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    # agora deve ser número com ponto
    return float(s)

def split_lines_table(txt: str):
    """
    Quebra por linha e detecta separador (tab, ;, ,) por linha.
    Retorna lista de listas (colunas).
    """
    rows = []
    for raw in (txt or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # tenta TAB primeiro
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        elif ";" in line:
            parts = [p.strip() for p in line.split(";")]
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
        else:
            # separação por múltiplos espaços
            parts = [p.strip() for p in re.split(r"\s{2,}|\s+", line) if p.strip()]
        rows.append(parts)
    return rows

def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def register_namespaces():
    ET.register_namespace("sb", "http://www.tesouro.gov.br/siafi/submissao")
    ET.register_namespace("dh", "http://services.docHabil.cpr.siafi.tesouro.fazenda.gov.br/")

NS_SB = "http://www.tesouro.gov.br/siafi/submissao"
NS_DH = "http://services.docHabil.cpr.siafi.tesouro.fazenda.gov.br/"

def sb(tag): return f"{{{NS_SB}}}{tag}"
def dh(tag): return f"{{{NS_DH}}}{tag}"

# -------------------------
# Parse blocks
# -------------------------

def parse_pco_items(texto_pco: str):
    """
    Colar 5 colunas: numEmpenho | subitem(2) | codSit | numClassA | valor
    Ex: 2026NE000055    46    DFL033    113110105    45905,55
    Retorna lista dict.
    """
    rows = split_lines_table(texto_pco)
    out = []
    for i, r in enumerate(rows, start=1):
        if len(r) < 5:
            raise ValueError(f"PCO linha {i} com colunas insuficientes (precisa 5): {r}")
        out.append({
            "numEmpe": r[0],
            "codSubItemEmpe": r[1].zfill(2),
            "codSit": r[2],
            "numClassA": only_digits(r[3]),
            "vlr": norm_decimal_br_to_dot(r[4]),
        })
    return out

def parse_outros_lanc(texto: str):
    """
    AGORA (como você corrigiu):
    4 colunas: codSit(PRV/LPA) | class3 (conta classe 3, só dígitos) | class2 (conta classe 2, só dígitos) | valor
    Ex: PRV001    311110122    211110101    223,89
    """
    rows = split_lines_table(texto)
    out = []
    for i, r in enumerate(rows, start=1):
        if len(r) < 4:
            raise ValueError(f"OutrosLanc linha {i} precisa 4 colunas: {r}")
        out.append({
            "codSit": r[0].strip().upper(),
            "numClassA": only_digits(r[1]),  # classe 3 -> numClassA
            "numClassD": only_digits(r[2]),  # classe 2 -> numClassD
            "vlr": norm_decimal_br_to_dot(r[3]),
        })
    return out

def parse_despesa_anular(texto: str):
    """
    Mesma estrutura do PCO, mas com valor NEGATIVO ou POSITIVO (aqui você cola já as linhas de anulação)
    5 colunas: numEmpenho | subitem(2) | codSit | numClassA | valor (positivo ou negativo)
    No XML despesaAnularItem deve ir com valor POSITIVO (anulação).
    """
    rows = split_lines_table(texto)
    out = []
    for i, r in enumerate(rows, start=1):
        if len(r) < 5:
            raise ValueError(f"DespesaAnular linha {i} precisa 5 colunas: {r}")
        v = norm_decimal_br_to_dot(r[4])
        out.append({
            "numEmpe": r[0],
            "codSubItemEmpe": r[1].zfill(2),
            "codSit": r[2],
            "numClassA": only_digits(r[3]),
            "vlr": abs(v),  # no XML vai positivo
        })
    return out

def parse_pgto(texto: str):
    """
    5 colunas: CNPJ | banco | agencia | txtCit | valor
    """
    rows = split_lines_table(texto)
    out = []
    for i, r in enumerate(rows, start=1):
        if len(r) < 5:
            raise ValueError(f"dadosPgto linha {i} precisa 5 colunas: {r}")
        out.append({
            "cnpj": only_digits(r[0]).zfill(14),
            "banco": r[1].zfill(3),
            "agencia": r[2].zfill(4),
            "txtCit": r[3],
            "vlr": norm_decimal_br_to_dot(r[4]),
        })
    return out

def parse_map_ndd(texto: str):
    """
    Cole CSV/TSV com 3 colunas:
      NDD | classe3 | classe2
    Ex:
      31.90.11.37    3.1.1.1.1.01.22    2.1.1.1.1.01.01
    Obs: o usuário disse que vai colar contas SEM separador; então vamos normalizar para dígitos.
    """
    rows = split_lines_table(texto)
    mapping = {}  # (class3_digits, class2_digits) -> ndd_digits
    for i, r in enumerate(rows, start=1):
        if len(r) < 3:
            continue
        ndd = only_digits(r[0])
        c3 = only_digits(r[1])
        c2 = only_digits(r[2])
        if c3 and c2 and ndd:
            mapping[(c3, c2)] = ndd
    return mapping

# -------------------------
# XML builder
# -------------------------

def build_xml(payload: dict) -> bytes:
    register_namespaces()

    # Envelope sb:arquivo
    root = ET.Element(sb("arquivo"))
    header = ET.SubElement(root, sb("header"))
    ET.SubElement(header, sb("codigoLayout")).text = payload["header"]["codigoLayout"]
    ET.SubElement(header, sb("dataGeracao")).text = payload["header"]["dataGeracao"]
    ET.SubElement(header, sb("sequencialGeracao")).text = str(int(payload["header"]["sequencialGeracao"]))
    ET.SubElement(header, sb("anoReferencia")).text = str(int(payload["header"]["anoReferencia"]))
    ET.SubElement(header, sb("ugResponsavel")).text = payload["header"]["ugResponsavel"]
    ET.SubElement(header, sb("cpfResponsavel")).text = payload["header"]["cpfResponsavel"]

    detalhes = ET.SubElement(root, sb("detalhes"))
    detalhe = ET.SubElement(detalhes, sb("detalhe"))

    cad = ET.SubElement(detalhe, dh("CprDhCadastrar"))
    ET.SubElement(cad, "codUgEmit").text = payload["cad"]["codUgEmit"]
    ET.SubElement(cad, "anoDH").text = str(int(payload["cad"]["anoDH"]))
    ET.SubElement(cad, "codTipoDH").text = payload["cad"]["codTipoDH"]
    # numDH é opcional; se vazio, não cria a tag
    if payload["cad"].get("numDH"):
        ET.SubElement(cad, "numDH").text = str(payload["cad"]["numDH"])

    # dadosBasicos
    db = ET.SubElement(cad, "dadosBasicos")
    for k in ["dtEmis","dtVenc","codUgPgto","vlr","txtObser","txtProcesso","dtAteste","codCredorDevedor","dtPgtoReceb"]:
        val = payload["dadosBasicos"][k]
        ET.SubElement(db, k).text = str(val)

    doc = ET.SubElement(db, "docOrigem")
    for k in ["codIdentEmit","dtEmis","numDocOrigem","vlr"]:
        ET.SubElement(doc, k).text = str(payload["docOrigem"][k])

    # pco (agrupado por codSit)
    pco_groups = payload["pco_groups"]  # list of dict {numSeqItem, codSit, codUgEmpe, items:[...]}
    for g in pco_groups:
        pco = ET.SubElement(cad, "pco")
        ET.SubElement(pco, "numSeqItem").text = str(g["numSeqItem"])
        ET.SubElement(pco, "codSit").text = g["codSit"]
        ET.SubElement(pco, "codUgEmpe").text = g["codUgEmpe"]

        for it in g["items"]:
            pit = ET.SubElement(pco, "pcoItem")
            ET.SubElement(pit, "numSeqItem").text = str(it["numSeqItem"])
            ET.SubElement(pit, "numEmpe").text = it["numEmpe"]
            ET.SubElement(pit, "codSubItemEmpe").text = it["codSubItemEmpe"]
            # XSD permite boolean. Use true/false.
            ET.SubElement(pit, "indrLiquidado").text = "true"
            ET.SubElement(pit, "vlr").text = f'{it["vlr"]:.2f}'
            ET.SubElement(pit, "numClassA").text = it["numClassA"]

    # outrosLanc (opcional)
    outros = payload.get("outrosLanc", [])
    for ol in outros:
        el = ET.SubElement(cad, "outrosLanc")
        ET.SubElement(el, "numSeqItem").text = str(ol["numSeqItem"])
        ET.SubElement(el, "codSit").text = ol["codSit"]
        ET.SubElement(el, "vlr").text = f'{ol["vlr"]:.2f}'
        # classe 3 (A) e classe 2 (D), conforme seu modelo
        if ol.get("numClassA"):
            ET.SubElement(el, "numClassA").text = ol["numClassA"]
        if ol.get("numClassD"):
            ET.SubElement(el, "numClassD").text = ol["numClassD"]

    # despesaAnular (opcional)
    for g in payload.get("despesa_anular_groups", []):
        da = ET.SubElement(cad, "despesaAnular")
        ET.SubElement(da, "numSeqItem").text = str(g["numSeqItem"])
        ET.SubElement(da, "codSit").text = g["codSit"]
        ET.SubElement(da, "codUgEmpe").text = g["codUgEmpe"]

        for it in g["items"]:
            dai = ET.SubElement(da, "despesaAnularItem")
            ET.SubElement(dai, "numSeqItem").text = str(it["numSeqItem"])
            ET.SubElement(dai, "numEmpe").text = it["numEmpe"]
            ET.SubElement(dai, "codSubItemEmpe").text = it["codSubItemEmpe"]
            ET.SubElement(dai, "vlr").text = f'{it["vlr"]:.2f}'
            ET.SubElement(dai, "numClassA").text = it["numClassA"]

    # centroCusto (opcional)
    for cc in payload.get("centros_custo", []):
        cce = ET.SubElement(cad, "centroCusto")
        ET.SubElement(cce, "numSeqItem").text = str(cc["numSeqItem"])
        ET.SubElement(cce, "codCentroCusto").text = cc["codCentroCusto"]
        ET.SubElement(cce, "mesReferencia").text = cc["mesReferencia"]
        ET.SubElement(cce, "anoReferencia").text = str(int(cc["anoReferencia"]))
        ET.SubElement(cce, "codUgBenef").text = cc["codUgBenef"]
        ET.SubElement(cce, "codSIORG").text = cc["codSIORG"]

        # relPcoItem
        for r in cc.get("relPcoItem", []):
            rp = ET.SubElement(cce, "relPcoItem")
            ET.SubElement(rp, "numSeqPai").text = str(r["numSeqPai"])
            ET.SubElement(rp, "numSeqItem").text = str(r["numSeqItem"])
            ET.SubElement(rp, "vlr").text = f'{r["vlr"]:.2f}'

        # relOutrosLanc (sem numSeqPai)
        for r in cc.get("relOutrosLanc", []):
            ro = ET.SubElement(cce, "relOutrosLanc")
            ET.SubElement(ro, "numSeqItem").text = str(r["numSeqItem"])
            if r.get("codNatDespDet"):
                ET.SubElement(ro, "codNatDespDet").text = r["codNatDespDet"]
            ET.SubElement(ro, "vlr").text = f'{r["vlr"]:.2f}'

        # relDespesaAnular
        for r in cc.get("relDespesaAnular", []):
            rd = ET.SubElement(cce, "relDespesaAnular")
            ET.SubElement(rd, "numSeqPai").text = str(r["numSeqPai"])
            ET.SubElement(rd, "numSeqItem").text = str(r["numSeqItem"])
            ET.SubElement(rd, "vlr").text = f'{r["vlr"]:.2f}'

    # dadosPgto (unbounded)
    for pg in payload.get("pagamentos", []):
        dp = ET.SubElement(cad, "dadosPgto")
        ET.SubElement(dp, "codCredorDevedor").text = pg["cnpj"]
        ET.SubElement(dp, "vlr").text = f'{pg["vlr"]:.2f}'
        predoc = ET.SubElement(dp, "predoc")
        ET.SubElement(predoc, "txtObser").text = payload["predoc_txtObser"]

        ob = ET.SubElement(predoc, "predocOB")
        ET.SubElement(ob, "codTipoOB").text = payload["codTipoOB"]
        ET.SubElement(ob, "codCredorDevedor").text = pg["cnpj"]
        ET.SubElement(ob, "txtCit").text = pg["txtCit"]

        fav = ET.SubElement(ob, "numDomiBancFavo")
        ET.SubElement(fav, "banco").text = pg["banco"]
        ET.SubElement(fav, "agencia").text = pg["agencia"]
        ET.SubElement(fav, "conta").text = payload["conta_fav"]

        pag = ET.SubElement(ob, "numDomiBancPgto")
        ET.SubElement(pag, "banco").text = payload["banco_pgto"]
        ET.SubElement(pag, "conta").text = payload["conta_pgto"]

    trailler = ET.SubElement(root, sb("trailler"))
    ET.SubElement(trailler, sb("quantidadeDetalhe")).text = "1"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

# -------------------------
# Streamlit UI
# -------------------------

st.set_page_config(page_title="Gerador LIQUIDAÇÃO (SIAFI) - XML", layout="wide")
st.title("Gerador de XML DH001 (DocHabil / CPR)")

tab_header, tab_db, tab_pco, tab_da, tab_outros, tab_pgto, tab_cc, tab_gerar = st.tabs([
    "Header/Arquivo", "Dados Básicos", "PCO (colar)", "Despesa a Anular (colar)", "Outros Lançamentos (colar)", "Dados Pgto (colar)", "Centro de Custo", "Gerar XML"
])

with tab_header:
    c1, c2, c3 = st.columns(3)
    with c1:
        codigoLayout = st.text_input("sb:codigoLayout", value="DH001")
        dataGeracao = st.text_input("sb:dataGeracao (dd/mm/aaaa)", value=date.today().strftime("%d/%m/%Y"))
        sequencialGeracao = st.number_input("sb:sequencialGeracao (sem zero à esquerda)", min_value=1, value=1, step=1)
    with c2:
        anoReferencia = st.number_input("sb:anoReferencia", min_value=2000, value=date.today().year, step=1)
        ugResponsavel = st.text_input("sb:ugResponsavel", value="120052")
        cpfResponsavel = st.text_input("sb:cpfResponsavel (somente dígitos)", value="09857528740")
    with c3:
        st.caption("Envelope `sb:arquivo` + `sb:header` + `sb:trailler`")

with tab_db:
    c1, c2, c3 = st.columns(3)
    with c1:
        codUgEmit = st.text_input("codUgEmit", value="120052")
        anoDH = st.number_input("anoDH", min_value=2000, value=date.today().year, step=1)
        codTipoDH = st.text_input("codTipoDH", value="FL")
        numDH = st.text_input("numDH (opcional; deixe vazio para NÃO criar a tag)", value="")
    with c2:
        dtEmis = st.text_input("dtEmis (aaaa-mm-dd)", value=str(date.today()))
        dtVenc = st.text_input("dtVenc (aaaa-mm-dd)", value=str(date.today()))
        codUgPgto = st.text_input("codUgPgto", value="120052")
        txtObser = st.text_input("txtObser", value="PAGAMENTO DA FOPAG JANEIRO/2026 CIVIL")
    with c3:
        txtProcesso = st.text_input("txtProcesso", value="67420.000835/2026-37")
        dtAteste = st.text_input("dtAteste (aaaa-mm-dd)", value=str(date.today()))
        codCredorDevedor = st.text_input("codCredorDevedor (UG ou inscrição)", value="120052")
        dtPgtoReceb = st.text_input("dtPgtoReceb (aaaa-mm-dd)", value=str(date.today()))

    st.divider()
    c4, c5 = st.columns(2)
    with c4:
        numDocOrigem = st.text_input("docOrigem/numDocOrigem", value="FOPAG.CIVL.JAN")
        codIdentEmit = st.text_input("docOrigem/codIdentEmit", value="120052")
        doc_dtEmis = st.text_input("docOrigem/dtEmis (aaaa-mm-dd)", value=str(date.today()))
    with c5:
        st.caption("⚠️ O valor líquido (`dadosBasicos/vlr`) será calculado a partir do PCO - DespesaAnular (OutrosLanc NÃO entra).")

with tab_pco:
    st.markdown("Cole **5 colunas**: `Empenho | Subitem | CodSit | Classificação (classe 3) | Valor`")
    txt_pco = st.text_area("PCO (colar aqui)", height=260, placeholder="2026NE000055\t46\tDFL033\t113110105\t45905,55")

with tab_da:
    st.markdown("Cole **5 colunas**: `Empenho | Subitem | CodSit | Classificação | Valor` (valor pode vir negativo; o XML vai em valor positivo).")
    txt_da = st.text_area("Despesa a Anular (colar aqui) — opcional", height=200, placeholder="2026NE000055\t46\tAFL033\t113110105\t-31381,26")

with tab_outros:
    st.markdown("Cole **4 colunas**: `Situação (PRV/LPA) | Classificação classe 3 | Classificação classe 2 | Valor`")
    st.markdown("Situações aceitas (validação): **PRV001, PRV002, PRV003, LPA385, LPA386**")
    txt_outros = st.text_area("Outros Lançamentos (colar aqui) — opcional", height=220, placeholder="PRV001\t311110122\t211110101\t223,89")

    st.divider()
    st.markdown("### Mapeamento (opcional) para preencher `codNatDespDet` no Centro de Custos")
    st.caption("Cole 3 colunas: `NDD | Classe 3 | Classe 2` (com ou sem pontos; o app normaliza para dígitos).")
    txt_map = st.text_area("Mapa NDD (opcional)", height=160, value="""31.90.11.37\t3.1.1.1.1.01.22\t2.1.1.1.1.01.01
31.90.11.38\t3.1.1.1.1.01.23\t2.1.1.1.1.01.01
31.90.11.39\t3.1.1.1.1.01.24\t2.1.1.1.1.01.01
31.90.11.41\t3.1.1.2.1.01.12\t2.1.1.1.1.01.01
31.90.11.42\t3.1.1.2.1.01.13\t2.1.1.1.1.01.01
31.90.11.43\t3.1.1.2.1.01.14\t2.1.1.1.1.01.01
31.90.11.61\t3.1.1.1.1.01.22\t2.1.1.1.1.01.01
31.90.11.62\t3.1.1.1.1.01.23\t2.1.1.1.1.01.01
""")

with tab_pgto:
    st.markdown("Cole **5 colunas**: `CNPJ | Banco | Agência | txtCit | Valor`")
    txt_pgto = st.text_area("Dados Pgto (colar aqui)", height=260, value="""00.000.000/0001-91\t001\t1607\t120052FPAG999\t57079618,21
90.400.888.0001-42\t033\t3403\t120052FPAG999\t11229017,11
92.702.067/0001-96\t041\t0335\t120052FPAG999\t64010,22
60.746.948/0001-12\t237\t0469\t120052FPAG00264\t8809742,47
60.701.190/0001-04\t341\t0284\t120052FPAG0284690005\t12651553,40
""")
    c1, c2, c3 = st.columns(3)
    with c1:
        codTipoOB = st.text_input("predocOB/codTipoOB", value="OBF")
    with c2:
        conta_fav = st.text_input("numDomiBancFavo/conta", value="FOPAG")
        predoc_txtObser = st.text_input("predoc/txtObser", value="PAGAMENTO FOPAG")
    with c3:
        banco_pgto = st.text_input("numDomiBancPgto/banco", value="002")
        conta_pgto = st.text_input("numDomiBancPgto/conta", value="UNICA")

with tab_cc:
    c1, c2, c3 = st.columns(3)
    with c1:
        codCentroCusto = st.text_input("codCentroCusto", value="221A00")
        mesReferencia = st.text_input("mesReferencia (MM)", value="01")
    with c2:
        anoCC = st.number_input("anoReferencia (CC)", min_value=2000, value=date.today().year, step=1)
        codUgBenef = st.text_input("codUgBenef", value="120052")
    with c3:
        codSIORG = st.text_input("codSIORG", value="2332")
    st.caption("O Centro de Custos será gerado automaticamente a partir de PCO/DespesaAnular/OutrosLanc.")

with tab_gerar:
    st.markdown("### Checagens e Geração")
    try:
        pco_all = parse_pco_items(txt_pco) if txt_pco.strip() else []
        da_all = parse_despesa_anular(txt_da) if txt_da.strip() else []
        outros_all_raw = parse_outros_lanc(txt_outros) if txt_outros.strip() else []
        pgto_all = parse_pgto(txt_pgto) if txt_pgto.strip() else []
        map_ndd = parse_map_ndd(txt_map) if txt_map.strip() else {}

        # Validações de situação OutrosLanc
        allowed_outros = {"PRV001","PRV002","PRV003","LPA385","LPA386"}
        for ol in outros_all_raw:
            if ol["codSit"] not in allowed_outros:
                st.warning(f"OutrosLanc codSit '{ol['codSit']}' fora do conjunto permitido {sorted(allowed_outros)}")

        # agrupar PCO por codSit (cada grupo vira um <pco>)
        pco_groups = {}
        for it in pco_all:
            pco_groups.setdefault(it["codSit"], []).append(it)

        pco_groups_list = []
        seq_pco = 1
        for codSit_g, items in pco_groups.items():
            # numSeqItem dentro do grupo começa em 1
            norm_items = []
            for j, it in enumerate(items, start=1):
                norm_items.append({**it, "numSeqItem": j})
            pco_groups_list.append({
                "numSeqItem": seq_pco,
                "codSit": codSit_g,
                "codUgEmpe": codUgEmit,
                "items": norm_items
            })
            seq_pco += 1

        # agrupar DespesaAnular por codSit
        da_groups = {}
        for it in da_all:
            da_groups.setdefault(it["codSit"], []).append(it)

        da_groups_list = []
        seq_da = 1
        for codSit_g, items in da_groups.items():
            norm_items = []
            for j, it in enumerate(items, start=1):
                norm_items.append({**it, "numSeqItem": j})
            da_groups_list.append({
                "numSeqItem": seq_da,
                "codSit": codSit_g,
                "codUgEmpe": codUgEmit,
                "items": norm_items
            })
            seq_da += 1

        # outrosLanc sequencia global (numSeqItem)
        outros_all = []
        for i, it in enumerate(outros_all_raw, start=1):
            outros_all.append({**it, "numSeqItem": i})

        # cálculos
        total_pco = sum(it["vlr"] for it in pco_all)
        total_da = sum(it["vlr"] for it in da_all)  # já em absoluto
        valor_liquido = round(total_pco - total_da, 2)

        total_outros = round(sum(it["vlr"] for it in outros_all), 2)
        total_pgto = round(sum(it["vlr"] for it in pgto_all), 2)

        # centro custo auto
        rel_pco = []
        for g in pco_groups_list:
            for it in g["items"]:
                rel_pco.append({"numSeqPai": g["numSeqItem"], "numSeqItem": it["numSeqItem"], "vlr": it["vlr"]})

        rel_da = []
        for g in da_groups_list:
            for it in g["items"]:
                rel_da.append({"numSeqPai": g["numSeqItem"], "numSeqItem": it["numSeqItem"], "vlr": it["vlr"]})

        rel_outros = []
        for it in outros_all:
            ndd = map_ndd.get((it["numClassA"], it["numClassD"]), "")
            rel_outros.append({"numSeqItem": it["numSeqItem"], "codNatDespDet": ndd, "vlr": it["vlr"]})

        soma_cc_liquido = round(sum(r["vlr"] for r in rel_pco) - sum(r["vlr"] for r in rel_da), 2)
        soma_cc_outros = round(sum(r["vlr"] for r in rel_outros), 2)

        # mostra checagens
        st.code(
f"""Checagens
Total PCO: {total_pco:.2f}
Total DespesaAnular: {total_da:.2f}
Valor líquido (PCO - DespesaAnular): {valor_liquido:.2f}

Soma Pagamentos (dadosPgto): {total_pgto:.2f}

CentroCusto (somente líquido = relPco - relDespesaAnular): {soma_cc_liquido:.2f}
CentroCusto (OutrosLanc = relOutrosLanc): {soma_cc_outros:.2f}
Total OutrosLanc: {total_outros:.2f}
""",
            language="text"
        )

        if round(total_pgto, 2) != round(valor_liquido, 2):
            st.warning("⚠️ Soma de pagamentos NÃO bate com o valor líquido (dadosBasicos).")
        else:
            st.success("✅ Soma de pagamentos bate com o valor líquido.")

        if round(soma_cc_liquido, 2) != round(valor_liquido, 2):
            st.warning("⚠️ Centro de custo (líquido) NÃO bate com o valor líquido. (Isso costuma gerar ER0237/ER0256).")
        else:
            st.success("✅ Centro de custo (líquido) bate com o valor líquido.")

        if outros_all and round(soma_cc_outros, 2) != round(total_outros, 2):
            st.warning("⚠️ Centro de custo (OutrosLanc) não bate com Total OutrosLanc.")
        elif outros_all:
            st.success("✅ Centro de custo (OutrosLanc) bate com Total OutrosLanc.")
        else:
            st.info("OutrosLanc vazio: a tag `outrosLanc` NÃO será gerada no XML.")

        # monta payload XML
        payload = {
            "header": {
                "codigoLayout": codigoLayout,
                "dataGeracao": dataGeracao,
                "sequencialGeracao": sequencialGeracao,
                "anoReferencia": anoReferencia,
                "ugResponsavel": ugResponsavel,
                "cpfResponsavel": only_digits(cpfResponsavel),
            },
            "cad": {
                "codUgEmit": codUgEmit,
                "anoDH": anoDH,
                "codTipoDH": codTipoDH,
                "numDH": numDH.strip() or None,
            },
            "dadosBasicos": {
                "dtEmis": dtEmis,
                "dtVenc": dtVenc,
                "codUgPgto": codUgPgto,
                "vlr": f"{valor_liquido:.2f}",
                "txtObser": txtObser,
                "txtProcesso": txtProcesso,
                "dtAteste": dtAteste,
                "codCredorDevedor": codCredorDevedor,
                "dtPgtoReceb": dtPgtoReceb,
            },
            "docOrigem": {
                "codIdentEmit": codIdentEmit,
                "dtEmis": doc_dtEmis,
                "numDocOrigem": numDocOrigem,
                "vlr": f"{valor_liquido:.2f}",
            },
            "pco_groups": pco_groups_list,
            "despesa_anular_groups": da_groups_list,
            "outrosLanc": outros_all,  # se vazio, não gera nada
            "centros_custo": [{
                "numSeqItem": 1,
                "codCentroCusto": codCentroCusto,
                "mesReferencia": mesReferencia.zfill(2),
                "anoReferencia": anoCC,
                "codUgBenef": codUgBenef,
                "codSIORG": codSIORG,
                "relPcoItem": rel_pco,
                "relOutrosLanc": rel_outros if outros_all else [],
                "relDespesaAnular": rel_da
            }],
            "pagamentos": pgto_all,
            "codTipoOB": codTipoOB,
            "predoc_txtObser": predoc_txtObser,
            "conta_fav": conta_fav,
            "banco_pgto": banco_pgto,
            "conta_pgto": conta_pgto,
        }

        xml_bytes = build_xml(payload)

        st.download_button(
            "⬇️ Baixar XML",
            data=xml_bytes,
            file_name=f"DH001_{ugResponsavel}_{dataGeracao.replace('/','')}.xml",
            mime="application/xml"
        )

        st.divider()
        st.markdown("#### Prévia (início do XML)")
        st.code(xml_bytes.decode("utf-8")[:2000], language="xml")

    except Exception as e:
        st.error(f"Erro ao processar dados: {e}")