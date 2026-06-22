"""Curated external sources. ``url`` is a template with {given} {surname} {place} {q} {year_from}
{year_to} placeholders (URL-encoded by the API). ``region`` is None for nationwide sources or a
province/CCAA name for regional ones. Extend per province/country over time — the structure is the
point. For now Spain (ES); the model already supports worldwide entries.
"""
from __future__ import annotations

CATEGORIES = {
    "tree": "Árboles y registros", "portal": "Archivos del Estado", "civil": "Registro civil",
    "parish": "Parroquiales", "military": "Militar y combatientes", "press": "Prensa y boletines",
    "official": "Boletines oficiales",
}

# Nationwide Spanish sources (region = None) + a couple of provincial examples.
SOURCES: list[dict] = [
    {"key": "familysearch", "name": "FamilySearch — registros", "category": "tree", "country": "ES",
     "region": None, "note": "Bautismos, matrimonios, defunciones, padrones (gratis).",
     "url": "https://www.familysearch.org/search/record/results?q.givenName={given}&q.surname={surname}&q.anyPlace={place}&q.birthLikeDate.from={year_from}&q.birthLikeDate.to={year_to}"},
    {"key": "familysearch_catalog", "name": "FamilySearch — catálogo (qué libros hay)", "category": "parish",
     "country": "ES", "region": None, "note": "Descubre qué libros parroquiales/civiles existen de un lugar.",
     "url": "https://www.familysearch.org/search/catalog/results?q.placeName={place}"},
    {"key": "pares", "name": "PARES — Portal de Archivos Españoles", "category": "portal", "country": "ES",
     "region": None, "note": "Archivo Histórico Nacional, Indias, Simancas, militar, Causa General…",
     "url": "https://pares.cultura.gob.es/ParesBusquedas20/catalogo/find?nm={q}"},
    {"key": "boe", "name": "BOE — Boletín Oficial del Estado", "category": "official", "country": "ES",
     "region": None, "note": "Indultos, naturalizaciones, oposiciones, expedientes, cambios de nombre.",
     "url": "https://www.boe.es/buscar/?q={q}"},
    {"key": "victimas_gc", "name": "Víctimas y combatientes (Guerra Civil)", "category": "military",
     "country": "ES", "region": None, "note": "Causa General, prisioneros, sumarísimos (vía PARES).",
     "url": "https://pares.cultura.gob.es/ParesBusquedas20/catalogo/find?nm={q}+guerra+civil"},
    {"key": "agms", "name": "Archivo General Militar — expedientes", "category": "military", "country": "ES",
     "region": None, "note": "Filiaciones, hojas de servicio, reemplazos (PARES / Defensa).",
     "url": "https://pares.cultura.gob.es/ParesBusquedas20/catalogo/find?nm={q}+militar"},
    {"key": "bne_hemeroteca", "name": "Hemeroteca Digital (BNE)", "category": "press", "country": "ES",
     "region": None, "note": "Prensa histórica: esquelas, bodas, noticias.",
     "url": "https://hemerotecadigital.bne.es/hd/es/results?text={q}"},
    {"key": "myheritage", "name": "MyHeritage — investigación", "category": "tree", "country": "ES",
     "region": None, "note": "Índices y árboles (mayoría de pago).",
     "url": "https://www.myheritage.es/research?action=query&formId=master&formMode=&qname=Name+fn.{given}+ln.{surname}"},
    {"key": "geneanet", "name": "Geneanet", "category": "tree", "country": "ES", "region": None,
     "note": "Árboles y archivos colaborativos.",
     "url": "https://www.geneanet.org/fonds/individus/?go=1&nom={surname}&prenom={given}&place={place}"},
    {"key": "ancestry", "name": "Ancestry", "category": "tree", "country": "ES", "region": None,
     "note": "Índices y registros (de pago).",
     "url": "https://www.ancestry.es/search/?name={given}_{surname}&location={place}"},
    # ── provincial examples (region set) ──
    {"key": "bop_badajoz", "name": "BOP Badajoz — Boletín Oficial de la Provincia", "category": "official",
     "country": "ES", "region": "Badajoz", "note": "Edictos, subastas, quintas, anuncios.",
     "url": "https://www.dip-badajoz.es/bop/index.php?accion=buscador&texto={q}"},
    {"key": "ahp_badajoz", "name": "Archivo Histórico Provincial de Badajoz", "category": "portal",
     "country": "ES", "region": "Badajoz", "note": "Protocolos notariales, civil, judicial.",
     "url": "https://pares.cultura.gob.es/ParesBusquedas20/catalogo/find?nm={q}+badajoz"},
]


def for_region(country: str | None, region: str | None) -> list[dict]:
    """Nationwide sources for the country + any whose region matches (case-insensitive substring)."""
    c = (country or "ES").upper()
    r = (region or "").strip().lower()
    out = []
    for s in SOURCES:
        if s["country"] != c:
            continue
        if s["region"] is None or (r and s["region"].lower() in r) or (r and r in s["region"].lower()):
            out.append(s)
    return out
