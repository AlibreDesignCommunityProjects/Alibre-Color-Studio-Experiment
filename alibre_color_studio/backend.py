"""Alibre Design side of Color Studio -- no GUI code lives here.

Everything talks to a *running* Alibre instance through ``alibrex``. The
module is import-safe when Alibre is closed: ``ALIBREX_SKIP_RUNNING_CHECK``
is set before ``alibrex`` is imported so the GUI can start, show a
disconnected state, and connect later from a button.

What can actually be colored
-----------------------------
AlibreX exposes color on three settable surfaces, and one read-only one:

======================  ==========================================  ========
Surface                 Properties                                  Settable
======================  ==========================================  ========
``IADPartSession``      Color, EdgeColor, Transparency,             yes
                        Reflectivity
``IADPartFeature``      FaceColor, EdgeColor, Opacity,              yes
                        Reflectivity, UsePartColor
``IADOccurrence``       Color, Transparency, Reflectivity           yes
``IADFace``             Color, GetColorForConfiguration             **no**
======================  ==========================================  ========

There is no API to set the color of an individual B-rep face -- ``IADFace.Color``
is get-only and AlibreX publishes no face-color feature constructor. A face
takes its color from the feature that produced it, which is also how Alibre's
own UI models it. So "color this face" is implemented honestly: the face is
resolved to its owning feature (via ``IADPartFeature.Faces`` and the stable
``IADFace.Key`` blob) and the feature is recolored, which repaints exactly the
faces that feature owns.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

os.environ.setdefault("ALIBREX_SKIP_RUNNING_CHECK", "1")

import alibrex
from alibrex import (
    IADAssemblySession,
    IADFace,
    IADOccurrence,
    IADPartFeature,
    IADPartSession,
    IADSurface,
    IADTargetProxy,
    narrow,
)
from alibrex._com_bridge import _ComProxy

from . import colors

MISSING = object()

def as_interface(obj: Any, interface: Any) -> Any:
    """View a COM object through ``interface``.

    ``IObjectCollector.Item`` is declared to return ``System.Object``, so
    alibrex hands back a bare ``System.__ComObject`` rather than one of its
    typed proxies. ``narrow`` only re-tags an existing proxy and raises
    ``AttributeError`` on a bare one, so selection results have to be wrapped
    explicitly -- this is the difference between reading Alibre's selection
    and silently seeing nothing in it.

    Wrapping never fails; using the result against the wrong interface raises
    ``TargetException``, which is what :func:`identify` keys off.
    """
    if obj is None or isinstance(obj, _ComProxy):
        return narrow(obj, interface) if obj is not None else None
    return _ComProxy(obj, interface)

def identify(obj: Any) -> tuple[str, Any]:
    """Classify a selected object as ``(kind, typed_proxy)``.

    ``kind`` is ``"face"``, ``"feature"``, ``"occurrence"`` or ``"unknown"``.
    Detection is by probing a property unique to each interface: AlibreX has
    no type tag on these, and reading through the wrong interface raises
    rather than returning a wrong answer, which makes this reliable.
    """
    for kind, interface, probe in (
        ("feature", IADPartFeature, lambda p: p.FaceColor),
        ("occurrence", IADOccurrence, lambda p: p.WorldTransform),
        ("face", IADFace, lambda p: p.Loops),
    ):
        typed = as_interface(obj, interface)
        try:
            probe(typed)
        except Exception:
            continue
        return kind, typed
    return "unknown", obj

def safe(fn: Callable[[], Any], default: Any = MISSING) -> Any:
    """Call ``fn``, swallowing the COM errors AlibreX raises liberally.

    Plenty of AlibreX properties throw rather than return a null --
    ``IADPartFeature.FeatureType`` raises ``COMException`` on weldment
    features, for instance -- so essentially every read needs a guard.
    """
    try:
        return fn()
    except Exception:
        return None if default is MISSING else default

def _count(collection: Any) -> int:
    """Length of an AlibreX collection, which may be ``None`` when empty."""
    if collection is None:
        return 0
    return safe(lambda: int(collection.Count), 0)

def _items(collection: Any) -> Iterable[Any]:
    for i in range(_count(collection)):
        item = safe(lambda i=i: collection.Item(i))
        if item is not None:
            yield item

@dataclass
class Document:
    """One open Alibre session that has something colorable in it."""

    index: int
    name: str
    kind: str
    subtype: str
    session: Any
    identifier: str = ""
    suffix: str = ""

    @property
    def label(self) -> str:
        tag = "ASM" if self.kind == "assembly" else "PRT"
        return f"[{tag}] {self.name}{self.suffix}"

@dataclass
class Target:
    """A single colorable thing inside a document."""

    key: str
    label: str
    kind: str
    ref: Any
    detail: str = ""
    face_count: int = 0

@dataclass
class FaceInfo:
    """One B-rep face of a feature, as shown in the tree view."""

    index: int
    rgb: colors.RGB
    kind: str = "Face"
    controller: str | None = None

    @property
    def label(self) -> str:
        return f"{self.kind} {self.index + 1}"

@dataclass
class ColorState:
    """The color-ish properties of a target, as read back from Alibre.

    ``None`` means "this surface doesn't expose that property" -- features
    have no transparency, only opacity; occurrences have no edge color.
    """

    rgb: colors.RGB = (0, 0, 0)
    edge_rgb: colors.RGB | None = None
    transparency: int | None = None
    reflectivity: int | None = None
    opacity: int | None = None
    use_part_color: bool | None = None

@dataclass
class Selection:
    """What Alibre currently has selected, sorted by how it was picked.

    ``from_faces`` and ``from_features`` both hold *feature* target keys --
    they differ only in how the user got there, which is what lets the two
    Apply buttons mean different things.
    """

    from_faces: list[str] = field(default_factory=list)
    from_features: list[str] = field(default_factory=list)
    occurrences: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def feature_keys(self) -> list[str]:
        """Every feature key, faces first, de-duplicated in order."""
        seen: set[str] = set()
        return [
            k
            for k in (*self.from_faces, *self.from_features)
            if not (k in seen or seen.add(k))
        ]

    @property
    def is_empty(self) -> bool:
        return not (self.from_faces or self.from_features or self.occurrences)

@dataclass
class Change:
    """One undoable edit: a target plus the state it had beforehand."""

    target_key: str
    target_label: str
    before: ColorState
    doc_index: int
    group: int = 0

@dataclass
class Backend:
    """Stateful facade over the running Alibre instance."""

    root: Any = None
    error: str = ""
    documents: list[Document] = field(default_factory=list)
    _undo: list[Change] = field(default_factory=list)
    _group: int = 0

    @property
    def connected(self) -> bool:
        return self.root is not None

    def connect(self) -> bool:
        """(Re)bind to the running Alibre instance.

        ``alibrex`` caches the bound root in two places; both are cleared so
        this doubles as a reconnect after Alibre has been restarted.
        """
        self.root = None
        self.error = ""
        safe(alibrex.reset_alibre)
        try:
            from alibrex import _com_bridge

            _com_bridge._BOUND_ROOT = None
        except Exception:
            pass
        try:
            self.root = alibrex.connect_to_running_alibre()
        except Exception as exc:
            self.root = None
            self.error = str(exc).strip() or type(exc).__name__
            return False
        return True

    def version(self) -> str:
        if not self.connected:
            return ""
        return str(safe(lambda: self.root.Version, "") or "")

    def refresh_documents(self) -> list[Document]:
        """Enumerate open sessions that expose a color surface.

        Deliberately duck-typed rather than gated on ``AD_PART``: weldment
        sessions report ``AD_WELDMENT`` yet carry the full part color API
        (``Color``, ``Features``, ``Bodies``), and ``alibrex.CurrentPart()``
        would reject them.
        """
        self.documents = []
        if not self.connected:
            return self.documents

        sessions = safe(lambda: self.root.Sessions)
        for i in range(_count(sessions)):
            session = safe(lambda i=i: sessions.Item(i))
            if session is None:
                continue
            name = safe(lambda: str(session.Name), "") or f"Session {i}"
            subtype = str(safe(lambda: session.SessionType, "") or "")

            ident = safe(lambda: str(session.Identifier), "") or ""

            asm = narrow(session, IADAssemblySession)
            if safe(lambda: asm.RootOccurrence) is not None:
                self.documents.append(Document(i, name, "assembly", subtype, asm, ident))
                continue

            part = narrow(session, IADPartSession)
            if safe(lambda: part.Color) is not None:
                self.documents.append(Document(i, name, "part", subtype, part, ident))

        self._disambiguate(self.documents)
        return self.documents

    @staticmethod
    def _disambiguate(documents: list[Document]) -> None:
        """Give same-named documents a distinguishing suffix.

        Without this, two sessions named "Weldment Sample 1" produce identical
        combo entries: DearPyGui warns about conflicting item IDs, and picking
        the second one resolves back to the first.
        """
        counts: dict[str, int] = {}
        for doc in documents:
            counts[doc.name] = counts.get(doc.name, 0) + 1
        seen: dict[str, int] = {}
        for doc in documents:
            if counts[doc.name] < 2:
                continue
            seen[doc.name] = seen.get(doc.name, 0) + 1
            doc.suffix = f"  #{seen[doc.name]}"

    def safe_topmost_identifier(self) -> str:
        """GUID of the frontmost Alibre session, or "" if unavailable."""
        if not self.connected:
            return ""
        top = safe(lambda: self.root.TopmostSession)
        if top is None:
            return ""
        return safe(lambda: str(top.Identifier), "") or ""

    def active_document_index(self) -> int:
        """Index into :attr:`documents` of whatever is frontmost in Alibre.

        Matched on the session GUID, not the name: several open documents can
        share a name, and matching by name would always land on the first.
        """
        top = safe(lambda: self.root.TopmostSession) if self.connected else None
        if top is None:
            return 0
        top_id = safe(lambda: str(top.Identifier), "")
        if top_id:
            for pos, doc in enumerate(self.documents):
                if doc.identifier and doc.identifier == top_id:
                    return pos
        top_name = safe(lambda: str(top.Name), "")
        for pos, doc in enumerate(self.documents):
            if doc.name == top_name:
                return pos
        return 0

    def targets(self, doc: Document) -> list[Target]:
        if doc.kind == "assembly":
            return self._assembly_targets(doc)
        return self._part_targets(doc)

    def _part_targets(self, doc: Document) -> list[Target]:
        session = doc.session
        out = [
            Target(
                key="part",
                label=doc.name,
                kind="part",
                ref=session,
                detail="whole part",
            )
        ]
        features = safe(lambda: session.Features)
        for i in range(_count(features)):
            feat = safe(lambda i=i: features.Item(i))
            if feat is None:
                continue
            feat = narrow(feat, IADPartFeature)
            name = safe(lambda: str(feat.Name), "") or f"Feature {i}"
            ftype = safe(lambda: str(feat.FeatureType), "") or ""
            nfaces = _count(safe(lambda: feat.Faces))
            detail = ftype.replace("AD_", "").replace("_FEATURE", "").replace("_", " ").title()
            if not detail:
                detail = "face color" if "color" in name.lower() else "feature"
            out.append(
                Target(
                    key=f"feature:{i}",
                    label=name,
                    kind="feature",
                    ref=feat,
                    detail=detail or "feature",
                    face_count=nfaces,
                )
            )
        return out

    def _assembly_targets(self, doc: Document) -> list[Target]:
        out: list[Target] = []
        rootocc = safe(lambda: doc.session.RootOccurrence)
        if rootocc is None:
            return out

        def walk(occ: Any, path: str, depth: int) -> None:
            children = safe(lambda: occ.Occurrences)
            n = _count(children)
            for i in range(n):
                child = safe(lambda i=i: children.Item(i))
                if child is None:
                    continue
                child = narrow(child, IADOccurrence)
                name = safe(lambda: str(child.Name), "") or f"Occurrence {i}"
                key = f"{path}/{i}"
                is_asm = _count(safe(lambda: child.Occurrences)) > 0
                out.append(
                    Target(
                        key=f"occ:{key}",
                        label=("    " * depth) + name,
                        kind="occurrence",
                        ref=child,
                        detail="sub-assembly" if is_asm else "part instance",
                    )
                )
                if is_asm and depth < 12:
                    walk(child, key, depth + 1)

        walk(rootocc, "", 0)
        return out

    def read(self, target: Target) -> ColorState:
        """Read a target's color properties.

        Unpacking is per-kind: part and feature colors use opposite byte
        orders (see :mod:`.colors`), so a single shared unpack would reverse
        red and blue on one of them.
        """
        ref = target.ref
        kind = target.kind

        def rgb_of(value: Any) -> colors.RGB | None:
            return None if value is None else colors.unpack_for(kind, value)

        if kind == "part":
            return ColorState(
                rgb=colors.unpack_for(kind, safe(lambda: ref.Color, 0) or 0),
                edge_rgb=rgb_of(safe(lambda: ref.EdgeColor)),
                transparency=safe(lambda: int(ref.Transparency)),
                reflectivity=safe(lambda: int(ref.Reflectivity)),
            )
        if kind == "feature":
            return ColorState(
                rgb=colors.unpack_for(kind, safe(lambda: ref.FaceColor, 0) or 0),
                edge_rgb=rgb_of(safe(lambda: ref.EdgeColor)),
                reflectivity=safe(lambda: int(ref.Reflectivity)),
                opacity=safe(lambda: int(ref.Opacity)),
                use_part_color=safe(lambda: bool(ref.UsePartColor)),
            )
        return ColorState(
            rgb=colors.unpack_for(kind, safe(lambda: ref.Color, 0) or 0),
            transparency=safe(lambda: int(ref.Transparency)),
            reflectivity=safe(lambda: int(ref.Reflectivity)),
        )

    def apply(
        self,
        target: Target,
        state: ColorState,
        doc: Document,
        record_undo: bool = True,
    ) -> list[str]:
        """Push ``state`` onto ``target``. Returns a list of failure notes.

        An empty list means every requested property was written. Properties
        left as ``None`` on ``state`` are not touched.
        """
        if record_undo:
            self._undo.append(
                Change(
                    target.key, target.label.strip(), self.read(target),
                    doc.index, self._group,
                )
            )

        ref = target.ref
        packed = colors.pack_for(target.kind, state.rgb)
        problems: list[str] = []

        def write(label: str, setter: Callable[[], None]) -> None:
            try:
                setter()
            except Exception as exc:
                problems.append(f"{label}: {type(exc).__name__}")

        if target.kind == "feature":
            if state.use_part_color is False:
                write("UsePartColor", lambda: setattr(ref, "UsePartColor", False))
            write("FaceColor", lambda: setattr(ref, "FaceColor", packed))
        else:
            write("Color", lambda: setattr(ref, "Color", packed))

        if state.edge_rgb is not None:
            write(
                "EdgeColor",
                lambda: setattr(
                    ref, "EdgeColor", colors.pack_for(target.kind, state.edge_rgb)
                ),
            )
        if state.transparency is not None:
            write(
                "Transparency",
                lambda: setattr(ref, "Transparency", max(0, min(99, state.transparency))),
            )
        if state.reflectivity is not None:
            write(
                "Reflectivity",
                lambda: setattr(ref, "Reflectivity", max(0, min(100, state.reflectivity))),
            )
        if state.opacity is not None:
            write("Opacity", lambda: setattr(ref, "Opacity", max(0, min(100, state.opacity))))

        if target.kind == "feature" and state.use_part_color is True:
            write("UsePartColor", lambda: setattr(ref, "UsePartColor", True))

        return problems

    def peek_undo(self) -> Change | None:
        return self._undo[-1] if self._undo else None

    def begin_group(self) -> None:
        """Start a new undo group; everything applied next undoes together."""
        self._group += 1

    def undo_last(self, targets_by_key: dict[str, Target], doc: Document) -> str:
        """Roll back the most recent user action, group and all."""
        if not self._undo:
            return "Nothing to undo."

        group = self._undo[-1].group
        batch: list[Change] = []
        while self._undo and self._undo[-1].group == group:
            batch.append(self._undo.pop())

        reverted, missing, problems = 0, 0, []
        for change in batch:
            target = targets_by_key.get(change.target_key)
            if target is None:
                missing += 1
                continue
            issues = self.apply(target, change.before, doc, record_undo=False)
            problems.extend(issues)
            reverted += 1

        if not reverted:
            return "Nothing to undo in this document."
        label = batch[-1].target_label if len(batch) == 1 else f"{reverted} target(s)"
        if problems:
            return f"Undo of {label} partly failed: {problems[0]}"
        skipped = f" ({missing} not in this document)" if missing else ""
        return f"Reverted {label}.{skipped}"

    def clear_undo(self) -> None:
        self._undo.clear()

    def face_to_feature_index(self, doc: Document) -> dict[bytes, int]:
        """Map each face to the feature that *controls* its color.

        ``IADFace.Key`` is an opaque byte blob, stable for a given face within
        a session, which makes it a usable dictionary key. Not every face in a
        body is reachable this way (imported and absorbed geometry often is
        not), so callers must handle a miss.

        A face is claimed by every feature that touches it, so precedence
        matters: the **last** claimant wins. Alibre models per-face color as a
        Face Color feature, which is created after the geometry it recolors and
        therefore sits later in the list. Taking the first claimant instead
        would always resolve to the base extrusion -- recoloring the whole
        feature and making the Face Color feature unreachable.
        """
        mapping: dict[bytes, int] = {}
        if doc.kind != "part":
            return mapping
        features = safe(lambda: doc.session.Features)
        for i in range(_count(features)):
            feat = safe(lambda i=i: features.Item(i))
            if feat is None:
                continue
            faces = safe(lambda: narrow(feat, IADPartFeature).Faces)
            for j in range(_count(faces)):
                face = safe(lambda j=j: faces.Item(j))
                blob = _face_key(face) if face is not None else None
                if blob is not None:
                    mapping[blob] = i
        return mapping

    def feature_faces(
        self,
        target: Target,
        limit: int = 500,
        controller_map: dict[bytes, int] | None = None,
    ) -> list[FaceInfo]:
        """The faces a feature owns, for the tree view.

        Read fresh each call: topology proxies expire, and a cached
        ``IADFace`` raises "Object no longer exists in server" once Alibre
        regenerates. ``limit`` keeps a pathological part from stalling the UI.
        """
        if target.kind != "feature":
            return []
        faces = safe(lambda: target.ref.Faces)
        own_index = int(target.key.split(":", 1)[1]) if ":" in target.key else -1
        out: list[FaceInfo] = []
        for i in range(min(_count(faces), limit)):
            face = safe(lambda i=i: faces.Item(i))
            if face is None:
                continue
            raw = safe(lambda: face.Color, 0) or 0
            surface = safe(lambda: str(narrow(face.Geometry, IADSurface).SurfaceType), "")
            kind = (surface or "").replace("AD_", "").title() or "Face"

            controller = None
            if controller_map is not None:
                blob = _face_key(face)
                index = controller_map.get(blob) if blob is not None else None
                if index is not None and index != own_index:
                    controller = f"feature:{index}"
            out.append(FaceInfo(i, colors.unpack_for("face", raw), kind, controller))
        return out

    def select_face_in_alibre(
        self, doc: Document, target: Target, face_index: int
    ) -> bool:
        """Select one specific face in Alibre, so the user sees what they picked."""
        if doc.kind != "part" or not self.connected:
            return False
        faces = safe(lambda: target.ref.Faces)
        face = safe(lambda: faces.Item(face_index)) if faces is not None else None
        if face is None:
            return False
        collector = safe(lambda: self.root.NewObjectCollector())
        if collector is None:
            return False
        try:
            collector.Add(face)
        except Exception:
            return False
        safe(lambda: doc.session.Select(collector))
        return True

    def feature_count(self, doc: Document) -> int:
        """How many features the document has right now (cheap poll)."""
        if doc.kind != "part":
            return 0
        return _count(safe(lambda: doc.session.Features))

    def part_face_count(self, doc: Document) -> int:
        """Total B-rep faces in the part, across every body."""
        if doc.kind != "part":
            return 0
        total = 0
        bodies = safe(lambda: doc.session.Bodies)
        for i in range(_count(bodies)):
            body = safe(lambda i=i: bodies.Item(i))
            if body is not None:
                total += _count(safe(lambda: body.Faces))
        return total

    def features_overriding_part(self, targets: list[Target]) -> list[Target]:
        """Features whose own color currently hides the part color.

        A feature with ``UsePartColor`` false keeps painting its own
        ``FaceColor``, so writing ``IADPartSession.Color`` changes nothing
        visible until these are handed back to the part.
        """
        out = []
        for target in targets:
            if target.kind != "feature":
                continue
            if safe(lambda t=target: bool(t.ref.UsePartColor)) is False:
                out.append(target)
        return out

    def resolve_selection(
        self, doc: Document, targets: list[Target] | None = None
    ) -> Selection:
        """Read Alibre's live selection and classify it.

        Faces resolve to the feature that owns them, since face color is not
        independently settable, but they are kept apart from directly-selected
        features so callers can tell the two apart.
        """
        result = Selection()
        selected = safe(lambda: doc.session.SelectedObjects)
        if _count(selected) == 0:
            result.notes.append("Nothing is selected in Alibre.")
            return result

        if targets is None:
            targets = self.targets(doc)
        by_feature_name = {t.label: t.key for t in targets if t.kind == "feature"}
        by_occ_name = {t.label.strip(): t.key for t in targets if t.kind == "occurrence"}

        face_map: dict[bytes, int] | None = None
        unmapped_faces: list[str] = []

        for raw in _items(selected):
            proxy = as_interface(raw, IADTargetProxy)
            display = safe(lambda: str(proxy.DisplayName), "") or "object"
            target_obj = safe(lambda: proxy.Target)
            if target_obj is None:
                target_obj = raw

            kind, typed = identify(target_obj)

            if kind == "face":
                if face_map is None:
                    face_map = self.face_to_feature_index(doc)
                blob = _face_key(typed)
                index = face_map.get(blob) if blob is not None else None
                if index is None:
                    unmapped_faces.append(display)
                else:
                    result.from_faces.append(f"feature:{index}")
                continue

            if kind == "feature":
                name = safe(lambda: str(typed.Name), "")
                if name in by_feature_name:
                    result.from_features.append(by_feature_name[name])
                else:
                    result.notes.append(f"{display}: feature is not in the current list.")
                continue

            if kind == "occurrence":
                name = safe(lambda: str(typed.Name), "")
                if name in by_occ_name:
                    result.occurrences.append(by_occ_name[name])
                else:
                    result.notes.append(f"{display}: component is not in the current list.")
                continue

            result.notes.append(f"{display} carries no color.")

        if unmapped_faces:
            shown = ", ".join(unmapped_faces[:3])
            more = f" (+{len(unmapped_faces) - 3})" if len(unmapped_faces) > 3 else ""
            result.notes.append(
                f"{shown}{more}: no owning feature - imported or absorbed "
                "geometry. Color the whole part instead."
            )

        if result.is_empty and not result.notes:
            result.notes.append("Selection contains nothing colorable.")

        for bucket in ("from_faces", "from_features", "occurrences"):
            seen: set[str] = set()
            setattr(
                result,
                bucket,
                [k for k in getattr(result, bucket) if not (k in seen or seen.add(k))],
            )
        return result

    def selection_target_keys(
        self, doc: Document, targets: list[Target] | None = None
    ) -> tuple[list[str], list[str]]:
        """Flat ``(keys, notes)`` view of :meth:`resolve_selection`."""
        found = self.resolve_selection(doc, targets)
        return [*found.feature_keys, *found.occurrences], found.notes

    def diagnose_selection(
        self, doc: Document, targets: list[Target] | None = None
    ) -> list[str]:
        """Walk the face -> feature chain, reporting every step.

        Written for the case where coloring a face does nothing and the
        status line alone doesn't say why: it names the document actually
        being read, what Alibre reports as selected, how each entry is
        classified, and whether its face key found an owning feature.
        """
        lines: list[str] = []
        if targets is None:
            targets = self.targets(doc)

        lines.append(f"Document: {doc.label}  (kind={doc.kind}, {doc.subtype})")
        top = safe(lambda: self.root.TopmostSession) if self.connected else None
        top_name = safe(lambda: str(top.Name), "?") if top is not None else "?"
        top_id = safe(lambda: str(top.Identifier), "") if top is not None else ""
        same = "yes" if top_id and top_id == doc.identifier else "NO"
        lines.append(f"Frontmost in Alibre: {top_name}  -- same document? {same}")
        if same == "NO":
            lines.append(
                "  >> You are reading a different document than the one you are "
                "clicking in. Switch the Document dropdown, or turn on "
                "'Follow active document'."
            )

        features = [t for t in targets if t.kind == "feature"]
        lines.append(f"Features listed: {len(features)}")
        for target in features:
            index = target.key.split(":", 1)[1]
            lines.append(
                f"  [{index}] {target.label.strip()}  ({target.detail}, "
                f"{target.face_count} faces)"
            )
        if not any("color" in t.label.lower() for t in features):
            lines.append(
                "  >> No Face Color feature is visible here. If you created one "
                "in Alibre (Part Tools > Face Color) and it is missing from this "
                "list, then AlibreX does not expose that feature type and "
                "per-face color cannot be edited from this app."
            )

        face_map = self.face_to_feature_index(doc)
        lines.append(f"Face -> feature map: {len(face_map)} face keys")
        if not face_map and doc.kind == "part":
            lines.append(
                "  >> No feature reports any faces. Face coloring cannot work "
                "here; color the whole part instead."
            )

        selected = safe(lambda: doc.session.SelectedObjects)
        count = _count(selected)
        lines.append(f"Alibre selection: {count} object(s)")
        if count == 0:
            lines.append("  >> Nothing selected. Click a face in Alibre first.")
            return lines

        for index, raw in enumerate(_items(selected)):
            proxy = as_interface(raw, IADTargetProxy)
            display = safe(lambda: str(proxy.DisplayName), None)
            target_obj = safe(lambda: proxy.Target)
            via = "TargetProxy.Target"
            if target_obj is None:
                target_obj, via = raw, "raw item (not a TargetProxy)"
            kind, typed = identify(target_obj)
            lines.append(f"  [{index}] {display or '<no DisplayName>'} via {via} -> {kind}")

            if kind == "face":
                blob = _face_key(typed)
                if blob is None:
                    lines.append("       face key unreadable")
                    continue
                hit = face_map.get(blob)
                if hit is None:
                    lines.append(
                        f"       key {blob[:6].hex()}... not in map -- no owning "
                        "feature (imported or absorbed geometry)"
                    )
                else:
                    label = next(
                        (t.label for t in targets if t.key == f"feature:{hit}"), "?"
                    )
                    lines.append(f"       -> feature:{hit} ({label})")
            elif kind == "unknown":
                lines.append("       carries no color this app can set")

        return lines

def _face_key(face: Any) -> bytes | None:
    """Hashable identity for a face, or ``None`` if ``face`` isn't one."""
    blob = safe(lambda: face.Key)
    if blob is None:
        return None
    try:
        return bytes(bytearray(blob))
    except Exception:
        return None
