"""Registry of genealogical document/record types and how to extract each (plan, extended).

The data model is deliberately *generic*: any source attaches facts to a named person via a
PersonMention + Citation, and type-specific fields land in ``Record.attributes`` (JSONB) so new
document types need no migration. This registry just gives the extraction LLM a per-type hint and
the roles/attributes typical of that type, and drives the UI's type picker.

Every type still produces the same shape (records → mentions), so linkage/coref work uniformly.
"""
from __future__ import annotations

# role vocabulary is open (String), but these are the suggested roles per family of document
SACRAMENTAL_ROLES = ["principal", "father", "mother", "godfather", "godmother", "spouse",
                     "spouse_father", "spouse_mother", "witness", "officiant", "declarant"]
CENSUS_ROLES = ["head", "spouse", "son", "daughter", "child", "father", "mother", "sibling",
               "grandparent", "grandchild", "in_law", "servant", "lodger", "relative", "other"]

RECORD_TYPES: dict[str, dict] = {
    "baptism": {"label": "Bautismo", "family": "sacramental", "roles": SACRAMENTAL_ROLES,
                "hint": "Bautizado, padres, abuelos, padrinos; fecha y parroquia."},
    "marriage": {"label": "Matrimonio", "family": "sacramental", "roles": SACRAMENTAL_ROLES,
                 "hint": "Cónyuges, sus padres, testigos; fecha, edades y naturaleza de cada uno."},
    "death": {"label": "Defunción", "family": "sacramental", "roles": SACRAMENTAL_ROLES,
              "hint": "Difunto, edad, cónyuge/padres si constan, causa, fecha y lugar."},
    "confirmation": {"label": "Confirmación", "family": "sacramental", "roles": SACRAMENTAL_ROLES,
                     "hint": "Confirmado, padres, padrino; fecha."},
    "census": {"label": "Censo / Padrón", "family": "census", "roles": CENSUS_ROLES,
               "hint": "UN registro por HOGAR. Captura la DIRECCIÓN/domicilio y, por cada conviviente, "
                       "nombre, EDAD, parentesco con el cabeza de familia, estado civil y oficio. "
                       "Agrupa por hogar: todos los que comparten domicilio van en el mismo registro."},
    "electoral_census": {"label": "Censo electoral", "family": "census", "roles": CENSUS_ROLES,
                         "hint": "Por elector: nombre, EDAD, DOMICILIO, profesión y nivel de instrucción. "
                                 "Agrupa por domicilio cuando se repita la dirección."},
    "will": {"label": "Testamento", "family": "notarial", "roles": ["testator", "heir", "spouse",
             "child", "executor", "witness", "notary", "relative", "other"],
             "hint": "Testador y TODOS los parientes nombrados (cónyuge, hijos, herederos) con su "
                     "relación EXPLÍCITA ('mi hijo', 'mi mujer'); legados y fecha."},
    "trial": {"label": "Juicio / causa judicial", "family": "judicial", "roles": ["defendant",
              "plaintiff", "witness", "judge", "victim", "relative", "other"],
              "hint": "Procesado/s, edad, oficio, domicilio, naturaleza; cargos, sentencia, fecha; "
                      "familiares mencionados. (Incluye juicios sumarísimos.)"},
    "military": {"label": "Ficha militar / quinta", "family": "military", "roles": ["soldier",
                 "father", "mother", "other"],
                 "hint": "Mozo/soldado: nombre, EDAD/fecha nacimiento, padres, naturaleza, domicilio, "
                         "filiación física, regimiento/reemplazo y fechas de servicio."},
    "residence": {"label": "Cambio de domicilio / empadronamiento", "family": "civil",
                  "roles": ["resident", "relative", "other"],
                  "hint": "Persona(s), DOMICILIO anterior y nuevo, fecha del cambio (p.ej. boletín oficial)."},
    "notarial": {"label": "Escritura notarial", "family": "notarial", "roles": ["party", "witness",
                 "notary", "relative", "other"],
                 "hint": "Partes, su relación y domicilio; objeto del acto y fecha."},
    "other": {"label": "Otro", "family": "other", "roles": ["principal", "relative", "other"],
              "hint": "Extrae toda persona nombrada y cualquier hecho genealógico (fechas, lugares, "
                      "parentescos, edad, domicilio, oficio)."},
}


def type_hint(record_type: str | None) -> str:
    rt = RECORD_TYPES.get(record_type or "", RECORD_TYPES["other"])
    return f"Tipo «{rt['label']}»: {rt['hint']}"
