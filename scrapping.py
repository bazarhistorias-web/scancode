# -*- coding: utf-8 -*-
"""
App: Scraper multi‑sitio para tiendas de cómics y mangas
Autor: ChatGPT

Cómo ejecutar localmente:
1) Crea y activa un entorno (opcional):
   python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
2) Instala dependencias:
   pip install streamlit requests beautifulsoup4 lxml pandas openpyxl urllib3
3) Ejecuta:
   streamlit run streamlit_scraper_comics_manga.py

Notas:
- Diseñado para páginas HTML estáticas. Para sitios 100% dinámicos (JS pesado), considera usar sus feeds, endpoints públicos o un renderizador como Playwright (no incluido por simplicidad).
- Respeta términos/robots de cada web.
"""

from __future__ import annotations
import re
from io import BytesIO
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from lxml import html as lxml_html
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

# =============== Utilidades HTTP ===============

def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    })
    return session

SESSION = make_session()

# =============== Heurísticas ===============

PRICE_PATTERN = re.compile(r"([\$€£]|\bCLP\b|\bMXN\b|\bARS\b|\bPEN\b|\bCOP\b)?\s*([0-9]{1,3}(?:[.,\s][0-9]{3})*(?:[.,][0-9]{2})?|[0-9]+)")
ISBN_PATTERN = re.compile(r"(?:ISBN(?:-1[03])?|SKU)\s*[:#]\s*([0-9Xx\- ]{8,20})")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_price(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.replace("\xa0", " ")
    m = PRICE_PATTERN.search(raw)
    if not m:
        return clean_text(raw)
    symbol, amount = m.groups(default="")
    # Quitar miles tipo 10.900,00 / 10,900.00 / 10900
    amount = amount.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        val = float(amount)
        # Muestra sin decimales si no aplica
        return f"{symbol + ' ' if symbol else ''}{val:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except ValueError:
        return clean_text(raw)


# =============== Extracción con selectores ===============

class Selector:
    def __init__(self, mode: str, query: str):
        self.mode = mode  # 'css' o 'xpath'
        self.query = query.strip()

    def is_empty(self) -> bool:
        return self.query == ""


def select_one_text(soup: BeautifulSoup, tree: lxml_html.HtmlElement, selector: Selector) -> str:
    if selector.is_empty():
        return ""
    if selector.mode == "css":
        el = soup.select_one(selector.query)
        return clean_text(el.get_text(" ") if el else "")
    else:
        nodes = tree.xpath(selector.query)
        if not nodes:
            return ""
        node = nodes[0]
        if hasattr(node, 'text_content'):
            return clean_text(node.text_content())
        return clean_text(str(node))


def select_one_attr(soup: BeautifulSoup, tree: lxml_html.HtmlElement, selector: Selector, attr: str) -> str:
    if selector.is_empty():
        return ""
    if selector.mode == "css":
        el = soup.select_one(selector.query)
        return el.get(attr, "") if el else ""
    else:
        nodes = tree.xpath(selector.query)
        if not nodes:
            return ""
        node = nodes[0]
        # lxml devuelve elementos o strings
        if hasattr(node, 'get'):
            return node.get(attr, "")
        return ""


# =============== Autodetección básica ===============

def auto_title(soup: BeautifulSoup, tree: lxml_html.HtmlElement) -> str:
    # 1) h1 principal
    h1 = soup.select_one("h1")
    if h1:
        t = clean_text(h1.get_text(" "))
        if len(t) > 0:
            return t
    # 2) og:title
    meta = soup.select_one('meta[property="og:title"]')
    if meta and meta.get("content"):
        return clean_text(meta.get("content"))
    # 3) title tag
    if soup.title and soup.title.string:
        return clean_text(soup.title.string)
    return ""


def auto_image(soup: BeautifulSoup, base_url: str) -> str:
    for q in [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'img.product, img.wp-post-image, img#product, img[class*="product"]',
        'img',
    ]:
        el = soup.select_one(q)
        if el:
            src = el.get("content") or el.get("src") or ""
            if src:
                return urljoin(base_url, src)
    return ""


def auto_description(soup: BeautifulSoup) -> str:
    meta = soup.select_one('meta[name="description"]')
    if meta and meta.get("content"):
        return clean_text(meta.get("content"))
    # Clases comunes
    for q in [
        ".product-short-description",
        ".woocommerce-product-details__short-description",
        ".descripcion, .description, #description, #sinopsis, .sinopsis",
        "article p",
        "p",
    ]:
        el = soup.select_one(q)
        if el:
            return clean_text(el.get_text(" "))
    return ""


def auto_price(soup: BeautifulSoup) -> str:
    # Meta schemas
    for q in ['meta[itemprop="price"]', 'meta[property="product:price:amount"]']:
        el = soup.select_one(q)
        if el and el.get("content"):
            return normalize_price(el.get("content"))
    # Selectores Woo/Shop comunes
    for q in [
        ".price .amount",
        "p.price",
        ".woocommerce-Price-amount",
        "[class*='price']",
    ]:
        el = soup.select_one(q)
        if el:
            return normalize_price(el.get_text(" "))
    # Cualquier número con símbolo
    body_text = soup.get_text(" ")
    m = PRICE_PATTERN.search(body_text)
    return normalize_price(m.group(0)) if m else ""


def auto_sku_isbn(soup: BeautifulSoup) -> str:
    # Busca patrones "ISBN" o "SKU" en el texto
    txt = soup.get_text(" ")
    m = ISBN_PATTERN.search(txt)
    if m:
        return clean_text(m.group(1))
    # Atributos comunes
    for q in [
        "span.sku",
        "#sku, .sku",
        "li:contains('ISBN'), li:contains('Sku'), tr:contains('ISBN'), tr:contains('SKU')",
    ]:
        try:
            el = soup.select_one(q)
            if el:
                return clean_text(el.get_text(" "))
        except Exception:
            pass
    return ""


def auto_ficha_tecnica(soup: BeautifulSoup) -> str:
    # Tablas o listas típicas con especificaciones
    for q in [
        "table.shop_attributes, table.woocommerce-product-attributes, table",
        ".product-attributes, .woocommerce-product-attributes, .ficha, .ficha-tecnica, #ficha",
        "dl, ul, ol",
    ]:
        el = soup.select_one(q)
        if el:
            # Texto compacto conservando pares clave:valor simples
            text = clean_text(el.get_text(" | "))
            return text
    return ""


# =============== Lógica de extracción por URL ===============

@st.cache_data(show_spinner=False)
def fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    """
    Devuelve sólo datos serializables por pickle para evitar errores de caché:
    - content (str)
    - base_url final (str)
    """
    resp = SESSION.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text, resp.url


def extract_from_url(url: str,
                     sel_title: Selector,
                     sel_sku: Selector,
                     sel_ficha: Selector,
                     sel_sinopsis: Selector,
                     sel_precio: Selector,
                     sel_imagen: Selector,
                     img_attr: str = "src",
                     autodetect: bool = True) -> dict:
    try:
        content, base_url = fetch(url)
soup = BeautifulSoup(content, "lxml")
tree = lxml_html.fromstring(content)
    except Exception as e:
        return {
            "url": url,
            "titulo": "",
            "sku_isbn": "",
            "ficha_tecnica": "",
            "sinopsis": "",
            "precio": "",
            "imagen": "",
            "error": f"HTTP/Parse: {e}"
        }

    # Selectores manuales
    title = select_one_text(soup, tree, sel_title)
    sku = select_one_text(soup, tree, sel_sku)
    ficha = select_one_text(soup, tree, sel_ficha)
    sinopsis = select_one_text(soup, tree, sel_sinopsis)
    precio_raw = select_one_text(soup, tree, sel_precio)
    imagen_rel = select_one_attr(soup, tree, sel_imagen, img_attr)

    if not imagen_rel and not sel_imagen.is_empty():
        # Si el selector manual apunta al elemento pero el src está en data-*, prueba data-src
        imagen_rel = select_one_attr(soup, tree, sel_imagen, "data-src") or select_one_attr(soup, tree, sel_imagen, "data-original")

    # Autodetección cuando falte algo
    if autodetect:
        if not title:
            title = auto_title(soup, tree)
        if not sku:
            sku = auto_sku_isbn(soup)
        if not ficha:
            ficha = auto_ficha_tecnica(soup)
        if not sinopsis:
            sinopsis = auto_description(soup)
        if not precio_raw:
            precio_raw = auto_price(soup)
        if not imagen_rel:
            imagen_rel = auto_image(soup, base_url)

    precio = normalize_price(precio_raw)
    imagen = urljoin(base_url, imagen_rel) if imagen_rel else ""

    return {
        "url": url,
        "titulo": title,
        "sku_isbn": sku,
        "ficha_tecnica": ficha,
        "sinopsis": sinopsis,
        "precio": precio,
        "imagen": imagen,
        "error": ""
    }


# =============== UI Streamlit ===============

st.set_page_config(page_title="Scraper Cómics & Manga", layout="wide")

st.title("🕸️ Scraper multi‑sitio: Cómics & Manga")
st.caption("Pega un listado de URLs de productos y define de dónde extraer cada campo. Si no defines un selector, se intentará autodetectar.")

with st.sidebar:
    st.header("⚙️ Configuración de selectores")
    st.write("Selecciona el **modo** (CSS o XPath) y pega el **selector** por campo.")

    mode = st.radio("Modo de selectores", ["css", "xpath"], horizontal=True, index=0)
    autodetect = st.checkbox("Rellenar con autodetección cuando falte info", value=True)

    st.subheader("Título")
    title_sel = st.text_input("Selector de título", placeholder="ej: h1.product_title")

    st.subheader("SKU / ISBN")
    sku_sel = st.text_input("Selector de SKU/ISBN", placeholder="ej: span.sku / //*[contains(text(),'ISBN')]")

    st.subheader("Ficha técnica")
    ficha_sel = st.text_area("Selector de Ficha Técnica", height=80, placeholder="ej: table.shop_attributes")

    st.subheader("Sinopsis / Descripción")
    sin_sel = st.text_area("Selector de Sinopsis", height=80, placeholder="ej: .woocommerce-product-details__short-description")

    st.subheader("Precio")
    price_sel = st.text_input("Selector de Precio", placeholder="ej: .price .amount")

    st.subheader("Imagen principal")
    img_sel = st.text_input("Selector de Imagen", placeholder="ej: .woocommerce-product-gallery__image img")
    img_attr = st.text_input("Atributo de imagen", value="src", help="Atributo a leer (p.ej. src, data-src)")

    st.divider()
    timeout = st.number_input("Timeout por URL (seg)", min_value=5, max_value=60, value=20)

# Entrada de URLs
col_left, col_right = st.columns([2, 1])
with col_left:
    urls_text = st.text_area("Pega las URLs (una por línea)", height=200, placeholder="https://tienda.com/producto-1\nhttps://otra.com/manga-2")
with col_right:
    uploaded = st.file_uploader("O sube un CSV/XLSX con columna 'url'", type=["csv", "xlsx"])
    sample = st.checkbox("Cargar ejemplo de prueba")

urls: list[str] = []
if sample:
    urls = [
        "https://example.com/",
    ]

if urls_text.strip():
    urls += [u.strip() for u in urls_text.splitlines() if u.strip()]

if uploaded is not None:
    try:
        if uploaded.name.endswith(".csv"):
            df_in = pd.read_csv(uploaded)
        else:
            df_in = pd.read_excel(uploaded)
        if "url" in df_in.columns:
            urls += [str(u).strip() for u in df_in["url"].dropna().tolist()]
        else:
            st.warning("El archivo no contiene una columna 'url'.")
    except Exception as e:
        st.error(f"No se pudo leer el archivo: {e}")

urls = list(dict.fromkeys(urls))  # unique y conserva orden

# Botón de extracción
if st.button("🚀 Extraer datos", type="primary"):
    if not urls:
        st.error("Agrega al menos una URL.")
    else:
        st.info(f"Procesando {len(urls)} URL(s)…")

        sel_title_obj = Selector(mode, title_sel)
        sel_sku_obj = Selector(mode, sku_sel)
        sel_ficha_obj = Selector(mode, ficha_sel)
        sel_sin_obj = Selector(mode, sin_sel)
        sel_precio_obj = Selector(mode, price_sel)
        sel_img_obj = Selector(mode, img_sel)

        rows = []
        progress = st.progress(0.0)
        for i, url in enumerate(urls, start=1):
            row = extract_from_url(
                url=url,
                sel_title=sel_title_obj,
                sel_sku=sel_sku_obj,
                sel_ficha=sel_ficha_obj,
                sel_sinopsis=sel_sin_obj,
                sel_precio=sel_precio_obj,
                sel_imagen=sel_img_obj,
                img_attr=img_attr,
                autodetect=autodetect,
            )
            rows.append(row)
            progress.progress(i / len(urls))

        df = pd.DataFrame(rows, columns=[
            "url", "titulo", "sku_isbn", "ficha_tecnica", "sinopsis", "precio", "imagen", "error"
        ])

        st.success("Extracción completa ✅")
        st.dataframe(df, use_container_width=True, height=400)

        # Descarga Excel
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="scraper")
        st.download_button(
            label="💾 Descargar Excel",
            data=buffer.getvalue(),
            file_name="scraper_comics_manga.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# Ayuda rápida
with st.expander("📘 Consejos de uso"):
    st.markdown(
        """
        - **CSS vs XPath**: si la web usa WooCommerce, CSS suele ser más simple (ejemplos: `h1.product_title`, `.price .amount`, `span.sku`).
        - **Autodetección**: si dejas un selector vacío, la app intentará encontrar el dato (og:title, meta description, `span.sku`, etc.).
        - **Precio**: se normaliza al formato con coma decimal (p.ej. `10.900,00`). Ajusta luego en tu Excel si necesitas otro formato.
        - **Imagen**: por defecto se usa el atributo `src`. Si la página carga diferido, prueba `data-src` o `data-original`.
        - **Dinámicos**: si el contenido se construye con JavaScript, prueba abrir el endpoint de datos (JSON) o considera un crawler con renderizado.
        """
    )
