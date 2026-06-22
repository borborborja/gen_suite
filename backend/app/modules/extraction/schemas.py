"""The extraction contract: Pydantic models that are the source of truth for the LLM's JSON-schema
output. One transcription page → zero or more records (acts/events), each with the people named and
the facts stated about them. Generic across document types (sacramental, census, will, trial,
military, residence…): type-specific fields land in ``attributes`` (plan, extended).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .record_types import RECORD_TYPES

RECORD_TYPE_KEYS = tuple(RECORD_TYPES.keys())


class StatedRelation(BaseModel):
    """A kin link a document states explicitly (e.g. a will: 'mi hijo Juan'; a census: 'hijo')."""
    relation: str = Field(description="parent/child/spouse/sibling/grandparent/… relative to this person")
    name: str | None = Field(default=None, description="the related person's name as written")


class ExtractedMention(BaseModel):
    role: str = Field(description="role in this act (e.g. principal, head, son, testator, defendant…)")
    given: str | None = None
    surname: str | None = None
    name_raw: str | None = Field(default=None, description="verbatim name exactly as written, incl. Latin form")
    sex: str | None = Field(default=None, description="M, F or U")
    stated_age: str | None = Field(default=None, description="age or birth date if stated")
    stated_origin: str | None = Field(default=None, description="naturaleza / birthplace if stated")
    stated_status: str | None = Field(default=None, description="civil status if stated")
    occupation: str | None = Field(default=None, description="profession/trade if stated")
    address: str | None = Field(default=None, description="domicile/street/house if stated (key for census)")
    relationships: list[StatedRelation] = Field(
        default_factory=list, description="explicit kin links stated for this person"
    )


class ExtractedRecord(BaseModel):
    record_type: str = Field(description="one of: " + ", ".join(RECORD_TYPE_KEYS))
    record_no: str | None = Field(
        default=None, description="número de acta/entrada tal como aparece ('45', '45 bis'); null si no está numerada"
    )
    continues_from_previous: bool = Field(
        default=False,
        description="true si esta entrada es la CONTINUACIÓN de un acta que empezó en la página anterior (la página arranca a media entrada, sin cabecera ni número)",
    )
    incomplete: bool = Field(
        default=False,
        description="true si esta entrada queda CORTADA por el borde inferior de la página y continúa en la siguiente",
    )
    date_raw: str | None = None
    date_year: int | None = None
    date_month: int | None = None
    date_day: int | None = None
    place_raw: str | None = None
    parish_raw: str | None = None
    address: str | None = Field(default=None, description="household/domicile address (census/residence/will)")
    household_key: str | None = Field(
        default=None, description="stable label for the household so co-residents group together (census)"
    )
    summary: str | None = Field(default=None, description="one-line plain-language summary in Spanish")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    attributes: dict = Field(default_factory=dict, description="type-specific fields (charges, regiment, legacy…)")
    mentions: list[ExtractedMention] = Field(default_factory=list)


class ExtractedPage(BaseModel):
    # Optional + defaulted: some models (e.g. Gemini Pro) return the records array but omit this flag.
    # The extraction task gates on whether `records` is non-empty, not on this boolean.
    has_record: bool = Field(default=True, description="false for blank/illegible pages")
    folio_label: str | None = Field(
        default=None, description="número de hoja/folio impreso o escrito en la página ('23v', 'fol. 145'); null si no lo hay"
    )
    records: list[ExtractedRecord] = Field(
        default_factory=list, description="actos en ORDEN DE LECTURA, de arriba a abajo"
    )


class IndexEntryExtracted(BaseModel):
    name_raw: str | None = Field(default=None, description="nombre tal como aparece en el índice")
    given: str | None = None
    surname: str | None = None
    folio_label: str | None = Field(default=None, description="folio/página a la que remite la entrada ('45', '23v')")
    record_no: str | None = Field(default=None, description="número de acta si el índice lo indica")
    year: int | None = None
    record_type: str | None = Field(default=None, description="baptism/marriage/death/… si se deduce")


class IndexPage(BaseModel):
    has_index: bool = Field(default=True, description="false si la página no es un índice")
    entries: list[IndexEntryExtracted] = Field(default_factory=list)


INDEX_SYSTEM_PROMPT = (
    "Esto es la página de un ÍNDICE de un libro parroquial: una lista (a menudo alfabética) que remite "
    "cada NOMBRE a un FOLIO/PÁGINA o número de acta del libro. Extrae CADA entrada del índice con: el "
    "nombre tal cual (name_raw) y, si puedes, given/surname; y el FOLIO o página al que remite "
    "(folio_label) y/o el número de acta (record_no). Si la entrada indica año o tipo, inclúyelos. "
    "No te inventes folios; deja null lo que no aparezca. Si la página NO es un índice, has_index=false. "
    "Devuelve estrictamente el JSON del esquema."
)


SYSTEM_PROMPT = (
    "Eres un paleógrafo y genealogista experto en documentos históricos españoles y catalanes "
    "(registros parroquiales, censos y padrones, testamentos, causas judiciales, fichas militares, "
    "empadronamientos y boletines oficiales), en español, catalán o latín.\n"
    "Extrae CADA acto/registro de la página y, dentro de cada uno, TODA persona nombrada con: rol, "
    "nombre (conserva la grafía original en 'name_raw', incl. forma latina), sexo, EDAD, naturaleza/"
    "origen, estado civil, OFICIO, DOMICILIO, y las RELACIONES de parentesco que el documento declare "
    "explícitamente (p. ej. 'su hijo', 'mi mujer'). Guarda en 'attributes' los datos propios del tipo "
    "(cargos y sentencia en un juicio; regimiento y reemplazo en una ficha militar; legados en un "
    "testamento; domicilio anterior/nuevo en un cambio de residencia).\n"
    "En CENSOS y PADRONES: crea UN registro por HOGAR, pon la dirección en 'address' y 'household_key', "
    "y lista a todos los convivientes como menciones con su edad y parentesco con el cabeza de familia.\n"
    "El campo 'role' DEBE ser una clave en INGLÉS de este vocabulario: principal, father, mother, "
    "spouse, godfather, godmother, witness, head, son, daughter, child, sibling, grandparent, "
    "servant, lodger, testator, heir, defendant, soldier, resident, other.\n"
    "NUMERACIÓN: si la página lleva un número de hoja/folio visible, ponlo en 'folio_label'. Si las "
    "actas están numeradas, pon el número de cada una en 'record_no' (tal cual, p. ej. '45', '45 bis').\n"
    "ENTRADAS PARTIDAS entre hojas: lista los actos en ORDEN DE LECTURA. Si la página EMPIEZA a media "
    "entrada (sin cabecera ni número, continúa una frase de la hoja anterior), marca esa primera "
    "entrada con continues_from_previous=true. Si la ÚLTIMA entrada queda CORTADA por el borde inferior "
    "y sigue en la página siguiente, márcala con incomplete=true. No rellenes lo que no se vea en esta "
    "hoja; el sistema unirá las dos mitades.\n"
    "OBLIGATORIO — PERSONAS: cada acto que nombre personas DEBE llevar su lista 'mentions' rellena; "
    "NUNCA devuelvas un acto con 'mentions' vacío si en el texto aparecen nombres. En un BAUTISMO "
    "extrae siempre, como mínimo: el bautizado (role=principal), el padre (father) y la madre (mother) "
    "si constan, y padrinos (godfather/godmother). En MATRIMONIO: los dos cónyuges (spouse) y sus padres. "
    "En DEFUNCIÓN: el difunto (principal) y cónyuge/padres si constan. Aunque la grafía esté deteriorada "
    "(errores de HTR), reconstruye los nombres legibles; no descartes una persona por una palabra ilegible.\n"
    "TIPO: usa el tipo indicado en el CONTEXTO DEL LIBRO salvo evidencia clara en contra; no marques "
    "'other' si el libro es de bautismos/matrimonios/defunciones.\n"
    "FECHAS: convierte la fecha del acto escrita en letra a números en date_year/date_month/date_day "
    "(p. ej. 'a diez y ocho de enero de mil setecientos sesenta y cuatro' → day=18, month=1, year=1764).\n"
    "No inventes datos que no aparezcan, pero SÍ extrae todo lo que aparezca. Marca has_record=false solo "
    "en páginas realmente en blanco o totalmente ilegibles. Devuelve estrictamente el JSON del esquema."
)
