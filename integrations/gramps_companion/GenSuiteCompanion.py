"""A dependency-free Gramps Desktop companion for Gen Suite's reviewed sync API.

The addon intentionally uses stdlib HTTP rather than a third-party package so copying the
directory into Gramps' plugin folder is enough.  It creates an explicit baseline before any
write and never resolves a server-side conflict itself.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gi.repository import Gtk
from gramps.gen.db import DbTxn
from gramps.gen.lib import ChildRef, Family, Name, Person
from gramps.gui.plug import tool


def _call(value, method, default=None):
    func = getattr(value, method, None)
    try:
        return func() if callable(func) else default
    except Exception:
        return default


def _key(value):
    return _call(value, "get_gramps_id") or _call(value, "get_handle")


def _name_value(name, method):
    return _call(name, method) or None


def _json_safe(value):
    """Make Gramps' serialized tuples and value objects safe for an API envelope."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


class GenSuiteCompanionOptions(tool.ToolOptions):
    """The dialog owns connection settings so secrets never enter the Gramps database."""

    def __init__(self, name, person_id=None):
        super().__init__(name, person_id)


class GenSuiteCompanion(tool.Tool):
    def __init__(self, dbstate, user, options_class, name, callback=None):
        super().__init__(dbstate, user, options_class, name, callback)
        self.db = dbstate.db
        self.window = Gtk.Window(title="Gen Suite Companion")
        self.window.set_border_width(12)
        self.window.set_default_size(620, 360)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)
        self.url = self._field(box, "URL base de la API", "https://gen-suite.example/api")
        self.token = self._field(box, "Token de API (gsk_…)", "", hidden=True)
        self.connection = self._field(box, "ID de conexión (vacío para crearla)", "")
        self.name = self._field(box, "Nombre de este árbol", "Gramps Desktop")
        buttons = Gtk.Box(spacing=8)
        box.pack_start(buttons, False, False, 0)
        for label, action in (
            ("Crear conexión", self._create_connection),
            ("Confirmar línea base", self._baseline),
            ("Subir a Gen Suite", self._push),
            ("Bajar desde Gen Suite", self._pull),
            ("Ver conflictos", self._conflicts),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", action)
            buttons.pack_start(button, False, False, 0)
        self.status = Gtk.Label(label="Configura un token de escritura y confirma la línea base.", xalign=0)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, True, True, 0)
        self.window.connect("destroy", lambda *_: None)
        self.window.show_all()

    def _field(self, parent, label, initial, hidden=False):
        row = Gtk.Box(spacing=8)
        parent.pack_start(row, False, False, 0)
        row.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_hexpand(True)
        if hidden:
            entry.set_visibility(False)
        row.pack_start(entry, True, True, 0)
        return entry

    def _config(self):
        url = self.url.get_text().strip().rstrip("/")
        token = self.token.get_text().strip()
        connection = self.connection.get_text().strip()
        if not url.startswith(("https://", "http://")) or not token.startswith("gsk_"):
            raise ValueError("Indica una URL de API válida y un token de API gsk_ de escritura.")
        if not connection:
            raise ValueError("Crea o pega primero el ID de conexión.")
        return url, token, connection

    def _request(self, method, path, data=None):
        url, token, _ = self._config()
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        request = Request(url + path, data=payload, method=method, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")) if response.length != 0 else None
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError("API %s: %s" % (error.code, detail))
        except URLError as error:
            raise RuntimeError("No se pudo conectar con Gen Suite: %s" % error.reason)

    def _run(self, label, work):
        self.status.set_text(label + "…")
        def worker():
            try:
                message = work()
            except Exception as error:  # surface a safe, actionable error in the dialog
                message = "Error: " + str(error)
            Gtk.idle_add(self.status.set_text, message)
        threading.Thread(target=worker, daemon=True).start()

    def _local_objects(self):
        """Stable minimal Gramps projection. Unknown server envelopes stay lossless on the API."""
        people = []
        for handle in self.db.get_person_handles():
            person = self.db.get_person_from_handle(handle)
            primary = _call(person, "get_primary_name")
            gender = _call(person, "get_gender")
            # Gramps has used enum constants as well as numeric values between releases.
            sex = "F" if str(gender).lower() in {"female", "0"} else "M" if str(gender).lower() in {"male", "1"} else "U"
            people.append({"object_type": "person", "object_key": _key(person), "operation": "upsert", "payload": {
                "sex": sex, "names": [{"given": _name_value(primary, "get_first_name"),
                                            "surname": _name_value(primary, "get_surname"), "primary": True}],
                "raw": _json_safe(_call(person, "serialize", [])),
            }})
        families = []
        for handle in self.db.get_family_handles():
            family = self.db.get_family_from_handle(handle)
            children = []
            for child_ref in _call(family, "get_child_ref_list", []) or []:
                child = self.db.get_person_from_handle(_call(child_ref, "get_reference_handle"))
                if child:
                    children.append(_key(child))
            father = self.db.get_person_from_handle(_call(family, "get_father_handle"))
            mother = self.db.get_person_from_handle(_call(family, "get_mother_handle"))
            families.append({"object_type": "family", "object_key": _key(family), "operation": "upsert", "payload": {
                "husband_key": _key(father) if father else None, "wife_key": _key(mother) if mother else None,
                "child_keys": children, "raw": _json_safe(_call(family, "serialize", [])),
            }})
        # Gramps has more than the person/family projection.  Include every primary object as an
        # opaque record too, so an unsupported editor in Gen Suite cannot erase its data.  Where a
        # human-readable field is universally available we expose it for the native projection.
        opaque = []
        for object_type, handles_name, getter_name, title_name in (
            ("event", "get_event_handles", "get_event_from_handle", "get_type"),
            ("place", "get_place_handles", "get_place_from_handle", "get_title"),
            ("source", "get_source_handles", "get_source_from_handle", "get_title"),
            ("repository", "get_repository_handles", "get_repository_from_handle", "get_name"),
            ("citation", "get_citation_handles", "get_citation_from_handle", None),
            ("media", "get_media_handles", "get_media_from_handle", None),
            ("note", "get_note_handles", "get_note_from_handle", None),
            ("tag", "get_tag_handles", "get_tag_from_handle", "get_name"),
        ):
            handles = _call(self.db, handles_name, []) or []
            getter = getattr(self.db, getter_name, None)
            if not callable(getter):
                continue
            for handle in handles:
                try:
                    item = getter(handle)
                except Exception:
                    item = None
                if not item:
                    continue
                payload = {"raw": _json_safe(_call(item, "serialize", []))}
                title = _call(item, title_name) if title_name else None
                if object_type == "place" and title:
                    payload["name"] = str(title)
                elif object_type == "source" and title:
                    payload["title"] = str(title)
                elif object_type == "repository" and title:
                    payload["name"] = str(title)
                opaque.append({"object_type": object_type, "object_key": _key(item), "operation": "upsert", "payload": payload})
        return people + families + opaque

    def _create_connection(self, *_):
        def work():
            url = self.url.get_text().strip().rstrip("/")
            token = self.token.get_text().strip()
            if not url.startswith(("https://", "http://")) or not token.startswith("gsk_"):
                raise ValueError("Indica una URL de API válida y un token gsk_.")
            request = Request(url + "/interop/connections", data=json.dumps({
                "provider": "gramps_desktop", "name": self.name.get_text().strip() or "Gramps Desktop",
            }).encode("utf-8"), method="POST", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urlopen(request, timeout=30) as response:
                created = json.loads(response.read().decode("utf-8"))
            Gtk.idle_add(self.connection.set_text, created["id"])
            return "Conexión creada. Confirma ahora la línea base."
        self._run("Creando conexión", work)

    def _baseline(self, *_):
        def work():
            _, _, connection = self._config()
            objects = self._local_objects()
            canonical = json.dumps(objects, sort_keys=True, separators=(",", ":")).encode("utf-8")
            counts = dict(Counter(item["object_type"] for item in objects))
            self._request("POST", "/interop/connections/%s/baselines" % connection, {
                "snapshot_hash": hashlib.sha256(canonical).hexdigest(), "object_counts": counts,
                "note": "Línea base creada por Gen Suite Companion",
            })
            return "Línea base confirmada (%s objetos)." % len(objects)
        self._run("Confirmando línea base", work)

    def _push(self, *_):
        def work():
            _, _, connection = self._config()
            objects = self._local_objects()
            for start in range(0, len(objects), 250):
                batch = {"changes": [dict(item, source_system="gramps_desktop") for item in objects[start:start + 250]]}
                self._request("POST", "/interop/connections/%s/changes" % connection, batch)
            result = self._request("POST", "/interop/connections/%s/apply" % connection)
            return "Subidos %s objetos; aplicados: %s; pendientes: %s." % (len(objects), result["applied"], result["deferred"])
        self._run("Subiendo cambios", work)

    def _find_person(self, key):
        try:
            return self.db.get_person_from_gramps_id(key)
        except Exception:
            return None

    def _apply_snapshot(self, objects):
        people = [item for item in objects if item.get("object_type") == "person" and item.get("operation") == "upsert"]
        families = [item for item in objects if item.get("object_type") == "family" and item.get("operation") == "upsert"]
        by_key = {}
        with DbTxn("Gen Suite: aplicar instantánea", self.db) as trans:
            for item in people:
                payload, key = item.get("payload") or {}, item.get("object_key")
                person = self._find_person(key)
                if not person:
                    person = Person()
                    try:
                        person.set_gramps_id(key)
                    except Exception:
                        pass
                names = payload.get("names") or [{}]
                primary = names[0] if isinstance(names[0], dict) else {}
                name = _call(person, "get_primary_name") or Name()
                name.set_first_name(primary.get("given") or "")
                name.set_surname(primary.get("surname") or "")
                person.set_primary_name(name)
                if _call(person, "get_handle"):
                    self.db.commit_person(person, trans)
                else:
                    self.db.add_person(person, trans)
                by_key[key] = person
            for item in families:
                payload, key = item.get("payload") or {}, item.get("object_key")
                family = None
                try:
                    family = self.db.get_family_from_gramps_id(key)
                except Exception:
                    pass
                if not family:
                    family = Family()
                    try:
                        family.set_gramps_id(key)
                    except Exception:
                        pass
                father = by_key.get(payload.get("husband_key")) or self._find_person(payload.get("husband_key"))
                mother = by_key.get(payload.get("wife_key")) or self._find_person(payload.get("wife_key"))
                family.set_father_handle(_call(father, "get_handle"))
                family.set_mother_handle(_call(mother, "get_handle"))
                for child_key in payload.get("child_keys") or []:
                    child = by_key.get(child_key) or self._find_person(child_key)
                    if child:
                        ref = ChildRef()
                        ref.set_reference_handle(_call(child, "get_handle"))
                        family.add_child_ref(ref)
                if _call(family, "get_handle"):
                    self.db.commit_family(family, trans)
                else:
                    self.db.add_family(family, trans)

    def _pull(self, *_):
        def work():
            _, _, connection = self._config()
            snapshot = self._request("GET", "/interop/connections/%s/snapshot" % connection)
            self._apply_snapshot(snapshot.get("objects", []))
            return "Instantánea recibida: personas y familias aplicadas; los demás objetos quedan preservados en Gen Suite."
        self._run("Bajando instantánea", work)

    def _conflicts(self, *_):
        def work():
            _, _, connection = self._config()
            changes = self._request("GET", "/interop/connections/%s/changes?state=conflict" % connection)
            return "%s conflictos pendientes. Resuélvelos en Gen Suite antes de volver a sincronizar." % len(changes)
        self._run("Consultando conflictos", work)
