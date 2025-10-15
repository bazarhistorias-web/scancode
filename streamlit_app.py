# -*- coding: utf-8 -*-
import io
import re
import hashlib
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st

# ===================== Config =====================
st.set_page_config(page_title="Armado de paquetes (lector de barras)", page_icon="📦", layout="wide")
st.title("📦 Armado de paquetes por factura (lector de barras)")
st.caption("Escanea códigos y te indicamos si va a Perú, Chile o Colombia. Banner grande persistente, validaciones y progreso por ISBN.")

# ===================== Utilidades =====================
def norm_code(x) -> str:
    if x is None:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(x)).upper()

def guess_col(df: pd.DataFrame, names: List[str]) -> Optional[str]:
    low = {c.lower(): c for c in df.columns}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None

def file_sig(uploaded_file) -> str:
    return hashlib.md5(uploaded_file.getvalue()).hexdigest()

def ensure_session():
    ss = st.session_state
    ss.setdefault("df", None)                 # DataFrame con tracking (mutado durante escaneo)
    ss.setdefault("base_cols", {})            # mapeo de columnas elegido
    ss.setdefault("file_sig", "")             # firma del archivo cargado
    ss.setdefault("scan_log", [])             # [{ts, code, match, destino, idx}]
    ss.setdefault("no_match", [])             # [{ts, code}]
    ss.setdefault("prioridad", ["Perú", "Chile", "Colombia"])
    ss.setdefault("incongruencias", pd.DataFrame())
    ss.setdefault("duplicados", pd.DataFrame())
    ss.setdefault("last_banner_html", "")     # <-- banner persistente entre reruns

ensure_session()

# ----- Banner grande -----
BANNER_CSS = """
<style>
.banner { width:100%; border-radius:16px; padding:22px 26px; margin:8px 0 16px 0;
  color:#0b0b0b; font-size:34px; font-weight:800; text-align:center;
  box-shadow:0 10px 24px rgba(0,0,0,0.12); border:2px solid rgba(0,0,0,0.08);}
.banner .small { display:block; font-size:14px; font-weight:600; opacity:.85; margin-top:8px; }
.banner.success { background:#d9fbe5; border-color:#1e7e34; }
.banner.warn    { background:#fff3cd; border-color:#ffb300; }
.banner.error   { background:#ffe0e0; border-color:#d32f2f; }
.banner.pe { background:#e8f5e9; border-color:#2e7d32; color:#1b5e20; }
.banner.cl { background:#e3f2fd; border-color:#1976d2; color:#0d47a1; }
.banner.co { background:#fff9e1; border-color:#fbc02d; color:#8d6e00; }
</style>
"""
def banner_html(text: str, variant: str = "success", subtitle: Optional[str] = None) -> str:
    sub = f'<span class="small">{subtitle}</span>' if subtitle else ""
    return f'{BANNER_CSS}<div class="banner {variant}">{text}{sub}</div>'

# ===================== Carga de factura (no reinicia contadores) =====================
c_left, c_right = st.columns([1, 1])
with c_left:
    up = st.file_uploader(
        "Factura en Excel (.xlsx). Ideal: A=ISBN, B=Nombre, C=Total, D=Perú, E=Chile, F=Colombia",
        type=["xlsx"], accept_multiple_files=False
    )
with c_right:
    st.markdown("**Prioridad por país** (si un título aparece en más de un país, se asigna al primero con cupo):")
    prioridad = st.multiselect("Orden de asignación", ["Perú", "Chile", "Colombia"], default=st.session_state.prioridad)
    if prioridad:
        st.session_state.prioridad = prioridad

if up:
    data = pd.read_excel(up, engine="openpyxl", header=0)
    sig = file_sig(up)

    st.subheader("Mapeo de columnas")
    st.caption("Si la autodetección falla, selecciona manualmente y pulsa **Aplicar/recargar archivo**.")

    g_isbn   = guess_col(data, ["isbn", "código", "codigo", "ean", "barra", "id"])
    g_nombre = guess_col(data, ["nombre", "titulo", "título", "descripcion", "descripción"])
    g_total  = guess_col(data, ["total", "cantidad", "comprados", "qty"])
    g_pe     = guess_col(data, ["peru", "perú"])
    g_cl     = guess_col(data, ["chile"])
    g_co     = guess_col(data, ["colombia"])

    cols = list(data.columns)
    def sbox(label: str, default: Optional[str], key: str) -> str:
        idx = cols.index(default) if (default in cols) else 0
        return st.selectbox(label, cols, index=min(idx, len(cols) - 1), key=key)

    cur = st.session_state.base_cols
    isbn_col   = sbox("Columna ISBN/EAN", cur.get("isbn")   or g_isbn   or cols[0], key="map_isbn")
    nombre_col = sbox("Columna Nombre",   cur.get("nombre") or g_nombre or (cols[1] if len(cols) > 1 else cols[0]), key="map_nom")
    total_col  = sbox("Columna Total",    cur.get("total")  or g_total  or (cols[2] if len(cols) > 2 else cols[-1]), key="map_tot")

    none_opt = "— (ninguna) —"
    pe_col = st.selectbox("Columna Perú", [none_opt] + cols,
                          index=(1 + cols.index(cur.get("peru", g_pe))) if cur.get("peru", g_pe) in cols else 0, key="map_pe")
    cl_col = st.selectbox("Columna Chile", [none_opt] + cols,
                          index=(1 + cols.index(cur.get("chile", g_cl))) if cur.get("chile", g_cl) in cols else 0, key="map_cl")
    co_col = st.selectbox("Columna Colombia", [none_opt] + cols,
                          index=(1 + cols.index(cur.get("colombia", g_co))) if cur.get("colombia", g_co) in cols else 0, key="map_co")

    apply_col1, apply_col2 = st.columns([1, 3])
    with apply_col1:
        apply = st.button("✅ Aplicar / recargar archivo")
    with apply_col2:
        st.caption("Se reconstruyen los cupos y **se reinician contadores** solo al aplicar o al cambiar de archivo.")

    need_reload = (sig != st.session_state.file_sig) or apply
    if need_reload:
        df = pd.DataFrame({
            "isbn":   data[isbn_col],
            "nombre": data[nombre_col],
            "total":  pd.to_numeric(data[total_col], errors="coerce").fillna(0).astype(int),
            "peru":   pd.to_numeric(data[pe_col] if pe_col != none_opt else 0, errors="coerce").fillna(0).astype(int),
            "chile":  pd.to_numeric(data[cl_col] if cl_col != none_opt else 0, errors="coerce").fillna(0).astype(int),
            "colombia": pd.to_numeric(data[co_col] if co_col != none_opt else 0, errors="coerce").fillna(0).astype(int),
        })
        df["isbn_norm"] = df["isbn"].map(norm_code)

        dup = df[df["isbn_norm"].duplicated(keep=False)].copy()
        suma_paises = df["peru"] + df["chile"] + df["colombia"]
        incong = df.loc[suma_paises != df["total"]].copy()
        st.session_state.duplicados = dup
        st.session_state.incongruencias = incong

        st.session_state.df = df.assign(
            sc_pe=0, sc_cl=0, sc_co=0, sc_total=0,
            rem_pe=lambda x: x["peru"],
            rem_cl=lambda x: x["chile"],
            rem_co=lambda x: x["colombia"],
        )
        st.session_state.base_cols = {
            "isbn": isbn_col, "nombre": nombre_col, "total": total_col,
            "peru": pe_col if pe_col != none_opt else None,
            "chile": cl_col if cl_col != none_opt else None,
            "colombia": co_col if co_col != none_opt else None,
        }
        st.session_state.file_sig = sig

        st.success(f"Factura aplicada. Filas: **{len(df)}** · Total esperado: **{int(df['total'].sum())}**")
        if not dup.empty:
            st.warning(f"⚠️ Hay **{len(dup)}** ISBN duplicados.")
        if not incong.empty:
            st.warning(f"⚠️ Hay **{len(incong)}** filas con incongruencia (PE+CL+CO ≠ Total).")

# ===================== Escaneo =====================
if st.session_state.df is not None:
    df = st.session_state.df

    colA, colB, colC, colD = st.columns(4)
    with colA: st.metric("Esperado (Total)", int(df["total"].sum()))
    with colB: st.metric("Escaneado", int(df["sc_total"].sum()))
    with colC: st.metric("Pendiente", int(df["total"].sum() - df["sc_total"].sum()))
    with colD: st.metric("No detectados", len(st.session_state.no_match))

    st.divider()
    st.subheader("📸 Escanear código de barras")

    # Banner persistente (repinta el último)
    banner_slot = st.empty()
    if st.session_state.last_banner_html:
        banner_slot.markdown(st.session_state.last_banner_html, unsafe_allow_html=True)

    auto_mode = st.checkbox("Auto-registrar al leer (no requiere ENTER)", value=True)

    def show_banner(text: str, variant: str = "success", subtitle: Optional[str] = None):
        html = banner_html(text, variant, subtitle)
        st.session_state.last_banner_html = html
        banner_slot.markdown(html, unsafe_allow_html=True)

    def elegir_slot_para_codigo(indices: List[int]) -> Tuple[Optional[int], Optional[str]]:
        for pais in st.session_state.prioridad:
            for i in indices:
                if pais == "Perú" and df.loc[i, "rem_pe"] > 0: return i, "Perú"
                if pais == "Chile" and df.loc[i, "rem_cl"] > 0: return i, "Chile"
                if pais == "Colombia" and df.loc[i, "rem_co"] > 0: return i, "Colombia"
        return None, None

    def registrar_codigo(raw_code: str):
        code = norm_code(raw_code)
        if not code:
            return
        ix_all = df.index[df["isbn_norm"] == code].tolist()
        ts = datetime.now().isoformat(timespec="seconds")

        if not ix_all:
            st.session_state.no_match.append({"ts": ts, "code": code})
            show_banner(f"NO DETECTADO: {code}", "error", "El código no está en la factura")
            return

        idx, destino = elegir_slot_para_codigo(ix_all)
        if idx is None:
            titulo = str(df.loc[ix_all[0], "nombre"])
            total_isbn = int(df.loc[ix_all, "total"].sum())
            total_esc  = int(df.loc[ix_all, "sc_total"].sum())
            st.session_state.scan_log.append({"ts": ts, "code": code, "match": True, "destino": "COMPLETO", "idx": ix_all[0]})
            show_banner(f"COMPLETO: {titulo}", "warn", f"Escaneado: {total_esc}/{total_isbn}")
            return

        # Actualizar contadores
        titulo = str(df.loc[idx, "nombre"])
        total_isbn = int(df.loc[ix_all, "total"].sum())
        total_esc  = int(df.loc[ix_all, "sc_total"].sum())
        if destino == "Perú":
            df.loc[idx, "sc_pe"] += 1; df.loc[idx, "rem_pe"] -= 1; variant = "pe"
        elif destino == "Chile":
            df.loc[idx, "sc_cl"] += 1; df.loc[idx, "rem_cl"] -= 1; variant = "cl"
        else:
            df.loc[idx, "sc_co"] += 1; df.loc[idx, "rem_co"] -= 1; variant = "co"
        df.loc[idx, "sc_total"] += 1

        # Progreso tras sumar
        total_esc += 1
        pe_esc = int(df.loc[ix_all, "sc_pe"].sum())
        cl_esc = int(df.loc[ix_all, "sc_cl"].sum())
        co_esc = int(df.loc[ix_all, "sc_co"].sum())
        st.session_state.scan_log.append({"ts": ts, "code": code, "match": True, "destino": destino, "idx": idx})

        destino_flag = destino.replace("Perú","🇵🇪 Perú").replace("Chile","🇨🇱 Chile").replace("Colombia","🇨🇴 Colombia")
        show_banner(destino_flag, variant, f"{titulo}<br>Escaneado: {total_esc}/{total_isbn} (PE {pe_esc} | CL {cl_esc} | CO {co_esc})")

    # Input de lectura (auto o manual)
    if auto_mode:
        def _on_change():
            v = st.session_state.get("scan_code", "")
            registrar_codigo(v)
            st.session_state.scan_code = ""  # limpiar para la próxima lectura
        st.text_input("Apunta el lector aquí (auto)", key="scan_code", on_change=_on_change, placeholder="ISBN/EAN…")
        st.caption("Deja el cursor aquí y dispara el lector; no necesitas ENTER.")
    else:
        with st.form("scan_form", clear_on_submit=True):
            code_in = st.text_input("Apunta el lector aquí y presiona Enter", placeholder="ISBN/EAN…")
            submit = st.form_submit_button("Registrar")
        if submit and code_in:
            registrar_codigo(code_in)

    # Controles
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    with c1:
        if st.button("↩️ Deshacer último escaneo"):
            if st.session_state.scan_log:
                last = st.session_state.scan_log.pop()
                if last["match"] and last["destino"] in {"Perú","Chile","Colombia"}:
                    i = last["idx"]
                    df.loc[i, "sc_total"] -= 1
                    if last["destino"] == "Perú":
                        df.loc[i, "sc_pe"] -= 1; df.loc[i, "rem_pe"] += 1
                    elif last["destino"] == "Chile":
                        df.loc[i, "sc_cl"] -= 1; df.loc[i, "rem_cl"] += 1
                    elif last["destino"] == "Colombia":
                        df.loc[i, "sc_co"] -= 1; df.loc[i, "rem_co"] += 1
                show_banner("Deshecho el último escaneo", "warn")
            else:
                st.warning("No hay escaneos para deshacer.")
    with c2:
        if st.button("🧹 Reiniciar escaneos"):
            df[["sc_pe","sc_cl","sc_co","sc_total"]] = 0
            df[["rem_pe","rem_cl","rem_co"]] = df[["peru","chile","colombia"]]
            st.session_state.scan_log.clear()
            st.session_state.no_match.clear()
            show_banner("Reiniciado", "warn", "Se reiniciaron contadores y registros")
    with c3:
        faltantes = df[(df["rem_pe"]>0) | (df["rem_cl"]>0) | (df["rem_co"]>0)]
        sobrantes = df[df["sc_total"] > df["total"]]
        alertas = []
        if not faltantes.empty: alertas.append(f"Faltan títulos/cantidades: **{len(faltantes)}**.")
        if not sobrantes.empty: alertas.append(f"Sobre-escaneos: **{len(sobrantes)}**.")
        if st.session_state.no_match: alertas.append(f"No detectados: **{len(st.session_state.no_match)}**.")
        if st.session_state.duplicados is not None and not st.session_state.duplicados.empty:
            alertas.append(f"Duplicados (archivo): **{len(st.session_state.duplicados)}**.")
        if st.session_state.incongruencias is not None and not st.session_state.incongruencias.empty:
            alertas.append(f"Incongruencias Total≠suma países: **{len(st.session_state.incongruencias)}**.")
        if alertas: st.warning(" | ".join(alertas))
        else: st.success("🎉 Todo validado: cantidades congruentes y sin no-detectados.")
    with c4:
        if st.button("🧼 Ocultar aviso"):
            st.session_state.last_banner_html = ""
            banner_slot.empty()

    st.divider()

    # Tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📋 Estado por título",
        "❗ No detectados",
        "🧾 Log de escaneos",
        "⚠️ Faltantes / Sobre-escaneos",
        "📑 Duplicados (archivo)",
        "🧮 Incongruencias (archivo)"
    ])

    with tab1:
        show = df[["isbn","nombre","total","peru","chile","colombia",
                   "sc_pe","sc_cl","sc_co","sc_total","rem_pe","rem_cl","rem_co"]]
        st.dataframe(show, use_container_width=True, height=420)

    with tab2:
        if st.session_state.no_match:
            st.dataframe(pd.DataFrame(st.session_state.no_match), use_container_width=True, height=300)
        else:
            st.success("Sin no-detectados por ahora.")

    with tab3:
        if st.session_state.scan_log:
            st.dataframe(pd.DataFrame(st.session_state.scan_log), use_container_width=True, height=300)
        else:
            st.info("Aún no hay escaneos registrados.")

    with tab4:
        faltantes = df[(df["rem_pe"] > 0) | (df["rem_cl"] > 0) | (df["rem_co"] > 0)]
        sobrantes = df[df["sc_total"] > df["total"]]
        st.write("**Faltantes** (remanentes > 0):")
        st.dataframe(faltantes, use_container_width=True, height=220)
        st.write("**Sobre-escaneos** (sc_total > total):")
        st.dataframe(sobrantes, use_container_width=True, height=220)

    with tab5:
        if st.session_state.duplicados is not None and not st.session_state.duplicados.empty:
            st.dataframe(st.session_state.duplicados, use_container_width=True, height=300)
        else:
            st.success("Sin duplicados en el archivo.")

    with tab6:
        if st.session_state.incongruencias is not None and not st.session_state.incongruencias.empty:
            st.dataframe(st.session_state.incongruencias, use_container_width=True, height=300)
        else:
            st.success("Sin incongruencias Total vs países.")

    # Descargas
    st.divider()
    d1, d2, d3 = st.columns(3)
    out_state = io.StringIO(); show.to_csv(out_state, index=False)
    d1.download_button("⬇️ Descargar estado (CSV)", out_state.getvalue().encode("utf-8"),
                       "estado_actual.csv", "text/csv")
    out_log = io.StringIO(); pd.DataFrame(st.session_state.scan_log).to_csv(out_log, index=False)
    d2.download_button("⬇️ Descargar log (CSV)", out_log.getvalue().encode("utf-8"),
                       "log_escaneos.csv", "text/csv")
    out_nf = io.StringIO(); pd.DataFrame(st.session_state.no_match).to_csv(out_nf, index=False)
    d3.download_button("⬇️ Descargar no detectados (CSV)", out_nf.getvalue().encode("utf-8"),
                       "no_detectados.csv", "text/csv")
