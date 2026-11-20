"""Reader for CPN Tools ``.cpn`` files.

File shape
----------
A ``.cpn`` file is XML with this skeleton (attributes elided)::

    <workspaceElements>
      <generator tool="CPN Tools" version="4.0.1" format="6"/>
      <cpnet>
        <globbox>                 <!-- declarations, grouped into blocks -->
          <block><id>...</id>
            <color id="..."><id>E</id>... <layout>colset E = ...;</layout></color>
            <var   id="..."><type><id>E</id></type><id>c</id><layout>var c : E;</layout></var>
            <ml    id="...">fun f x = ...<layout>fun f x = ...</layout></ml>
          </block>
        </globbox>
        <page id="...">
          <pageattr name="Top"/>
          <place id="..."> ... </place>
          <trans id="..."> ... </trans>
          <arc  id="..." orientation="PtoT"> ... </arc>
        </page>
        <instances/> <options/> <binders/> <monitorblock/>
      </cpnet>
    </workspaceElements>

Robustness strategy
-------------------
CPN Tools has emitted several format revisions and third-party tools emit
near-misses, so the reader is deliberately forgiving:

* unknown elements and attributes are ignored, not rejected;
* every field has a sensible default, so a missing ``<lineattr>`` costs you a
  line colour, not the whole file;
* declaration text is taken from the ``<layout>`` element whenever present,
  because that is the exact source the modeller typed.  Only if it is missing
  do we reconstruct the declaration from the structured sub-elements.

The DTD is referenced in the file's DOCTYPE but is not fetched: parsing runs
with entity resolution off, so a ``.cpn`` file cannot make us open a network
connection or read a local file.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

from ..model.declarations import parse_variable_declaration
from ..model.net import Arc, CPNet, Graphics, Page, Place, Transition


# ---------------------------------------------------------------------------
# Small helpers for pulling values out of the tree
# ---------------------------------------------------------------------------
def _text_of(element: ElementTree.Element | None, default: str = "") -> str:
    """Text content of an element, with whitespace trimmed."""
    if element is None or element.text is None:
        return default
    return element.text.strip()

def _child_text(parent: ElementTree.Element, tag: str, default: str = "") -> str:
    """Text of ``parent/tag/text`` -- the shape CPN Tools uses for inscriptions."""
    child = parent.find(tag)
    if child is None:
        return default
    inner = child.find("text")
    if inner is not None:
        return _text_of(inner, default)
    return _text_of(child, default)


def _float_attribute(element: ElementTree.Element | None, name: str, default: float) -> float:
    if element is None:
        return default
    raw = element.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_graphics(element: ElementTree.Element,
                   shape_tag: str | None = None) -> Graphics:
    """Read the ``posattr`` / ``fillattr`` / ``lineattr`` / ``textattr`` group.

    ``shape_tag`` is ``"ellipse"`` for places and ``"box"`` for transitions;
    both carry the width and height as ``w`` and ``h``.
    """
    graphics = Graphics()
    position = element.find("posattr")
    graphics.x = _float_attribute(position, "x", 0.0)
    graphics.y = _float_attribute(position, "y", 0.0)

    fill = element.find("fillattr")
    if fill is not None:
        graphics.fill_colour = fill.get("colour", graphics.fill_colour)

    line = element.find("lineattr")
    if line is not None:
        graphics.line_colour = line.get("colour", graphics.line_colour)
        try:
            graphics.line_width = float(line.get("thick", graphics.line_width))
        except ValueError:
            pass

    text = element.find("textattr")
    if text is not None:
        graphics.text_colour = text.get("colour", graphics.text_colour)

    if shape_tag:
        shape = element.find(shape_tag)
        graphics.width = _float_attribute(shape, "w", graphics.width)
        graphics.height = _float_attribute(shape, "h", graphics.height)

    # Where the modeller put each inscription (the centre of its text), kept
    # as an offset from the element so the label moves with it.  Arcs have no
    # position of their own: their offsets are absolute and the canvas turns
    # them into offsets from the arc's midpoint.
    for tag in ("type", "initmark", "cond", "time", "annot"):
        region = element.find(tag)
        if region is None:
            continue
        region_position = region.find("posattr")
        if region_position is None:
            continue
        x = _float_attribute(region_position, "x", 0.0)
        y = _float_attribute(region_position, "y", 0.0)
        if shape_tag:
            graphics.label_offsets[tag] = (x - graphics.x, y - graphics.y)
        else:
            graphics.label_offsets[tag] = (x, y)

    # The current-marking bubble (``token``) and text (``marking``) of a place.
    for tag in ("token", "marking"):
        region = element.find(tag)
        if region is not None:
            graphics.label_offsets[tag] = (_float_attribute(region, "x", 0.0),
                                           _float_attribute(region, "y", 0.0))
    marking = element.find("marking")
    if marking is not None:
        graphics.marking_hidden = marking.get("hidden", "false").strip().lower() == "true"

    return graphics


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------
def _reconstruct_colour_set_declaration(element: ElementTree.Element) -> str:
    """Rebuild ``colset NAME = ...;`` from the structured XML.

    Only used when the ``<layout>`` element is absent.  We cover the common
    forms; anything exotic falls back to an alias of ``UNIT`` so that the model
    still loads and the problem shows up as a compile issue rather than a
    crash.
    """
    name = _text_of(element.find("id"), "UNNAMED")
    timed = " timed" if element.find("timed") is not None else ""

    if element.find("enum") is not None:
        constants = [_text_of(i) for i in element.find("enum").findall("id")]
        return f"colset {name} = with {' | '.join(constants)}{timed};"

    if element.find("product") is not None:
        parts = [_text_of(i) for i in element.find("product").findall("id")]
        return f"colset {name} = product {' * '.join(parts)}{timed};"

    if element.find("record") is not None:
        fields = []
        for record_field in element.find("record").findall("recordfield"):
            fields.append(
                f"{_text_of(record_field.find('id'))}:{_text_of(record_field.find('id'), '')}"
            )
        return f"colset {name} = record {' * '.join(fields)}{timed};"

    if element.find("list") is not None:
        inner = _text_of(element.find("list").find("id"), "UNIT")
        return f"colset {name} = list {inner}{timed};"

    if element.find("index") is not None:
        index = element.find("index")
        tag = _text_of(index.find("id"), "idx")
        bounds = [_text_of(m) for m in index.findall("ml")]
        low, high = (bounds + ["1", "1"])[:2]
        return f"colset {name} = index {tag} with {low}..{high}{timed};"

    if element.find("union") is not None:
        variants = []
        for union_field in element.find("union").findall("unionfield"):
            tag = _text_of(union_field.find("id"))
            payload = union_field.find("id[2]")
            variants.append(f"{tag}:{_text_of(payload)}" if payload is not None else tag)
        return f"colset {name} = union {' + '.join(variants)}{timed};"

    for simple in ("unit", "bool", "int", "intinf", "real", "string"):
        if element.find(simple) is not None:
            return f"colset {name} = {simple}{timed};"

    # `alias` or anything unrecognised.
    alias = element.find("alias")
    if alias is not None:
        return f"colset {name} = {_text_of(alias.find('id'), 'UNIT')}{timed};"
    return f"colset {name} = UNIT{timed};"


def _read_globbox(globbox: ElementTree.Element, net: CPNet) -> None:
    """Walk the declaration tree, in document order, filling the model.

    Blocks may nest, so this recurses.  Order is preserved because a colour set
    may refer to earlier ones.
    """

    def visit(node: ElementTree.Element) -> None:
        for child in node:
            tag = child.tag
            if tag == "block":
                visit(child)
            elif tag == "color":
                layout = _text_of(child.find("layout"))
                source = layout or _reconstruct_colour_set_declaration(child)
                name = _text_of(child.find("id"), "UNNAMED")
                net.declarations.colour_set_sources.append((name, source))
            elif tag == "var":
                layout = _text_of(child.find("layout"))
                if layout:
                    try:
                        for declaration in parse_variable_declaration(layout):
                            net.declarations.variable_sources.append(
                                (declaration.name, declaration.colour_set_name, layout)
                            )
                        continue
                    except Exception:
                        pass  # fall through to the structured form
                colour_set_name = _text_of(child.find("type/id"), "UNIT")
                for identifier in child.findall("id"):
                    variable_name = _text_of(identifier)
                    if variable_name:
                        net.declarations.variable_sources.append(
                            (variable_name, colour_set_name,
                             f"var {variable_name} : {colour_set_name};")
                        )
            elif tag == "ml":
                layout = _text_of(child.find("layout"))
                source = layout or _text_of(child)
                if source:
                    net.declarations.ml_sources.append(source)
            elif tag == "globref":
                name = _text_of(child.find("id"), "")
                initial = _text_of(child.find("ml"), "0")
                if name:
                    net.declarations.globref_sources.append((name, initial))

    visit(globbox)


# ---------------------------------------------------------------------------
# Net elements
# ---------------------------------------------------------------------------
def _read_place(element: ElementTree.Element) -> Place:
    place = Place(
        id=element.get("id", ""),
        name=_text_of(element.find("text")),
        colour_set_name=_child_text(element, "type", "UNIT"),
        initial_marking_text=_child_text(element, "initmark", ""),
        graphics=_read_graphics(element, "ellipse"),
        source_element=element,
    )
    port = element.find("port")
    if port is not None:
        place.port_type = port.get("type", "General")
    fusion = element.find("fusioninfo")
    if fusion is not None:
        place.fusion_group = fusion.get("name")
    return place


def _read_transition(element: ElementTree.Element) -> Transition:
    transition = Transition(
        id=element.get("id", ""),
        name=_text_of(element.find("text")),
        guard_text=_child_text(element, "cond", ""),
        time_text=_child_text(element, "time", ""),
        code_text=_child_text(element, "code", ""),
        priority_text=_child_text(element, "priority", ""),
        graphics=_read_graphics(element, "box"),
        source_element=element,
    )
    substitution = element.find("subst")
    if substitution is not None:
        transition.substitution_subpage = substitution.get("subpage")
        # `portsock` is a flat string of alternating socket/port ids in
        # parentheses, e.g. "(ID1,ID2)(ID3,ID4)".
        raw = substitution.get("portsock", "")
        for pair in raw.replace(")", ")\n").split():
            cleaned = pair.strip("()")
            if "," in cleaned:
                socket, port = cleaned.split(",", 1)
                transition.port_assignments[socket.strip()] = port.strip()
    return transition


def _read_arc(element: ElementTree.Element) -> Arc:
    place_end = element.find("placeend")
    transition_end = element.find("transend")
    arc = Arc(
        id=element.get("id", ""),
        place_id=place_end.get("idref", "") if place_end is not None else "",
        transition_id=transition_end.get("idref", "") if transition_end is not None else "",
        orientation=element.get("orientation", "PtoT"),
        expression_text=_child_text(element, "annot", ""),
        graphics=_read_graphics(element),
        source_element=element,
    )
    for bendpoint in element.findall("bendpoint"):
        position = bendpoint.find("posattr")
        arc.bendpoints.append(
            (_float_attribute(position, "x", 0.0), _float_attribute(position, "y", 0.0))
        )
    return arc


def _read_page(element: ElementTree.Element) -> Page:
    attributes = element.find("pageattr")
    page = Page(
        id=element.get("id", ""),
        name=attributes.get("name", "Page") if attributes is not None else "Page",
        source_element=element,
    )
    page.places = [_read_place(e) for e in element.findall("place")]
    page.transitions = [_read_transition(e) for e in element.findall("trans")]
    arcs = [_read_arc(e) for e in element.findall("arc")]
    # Files edited in CPN Tools sometimes keep arcs whose place or transition
    # was deleted (the course's PlaneBoardingExample.cpn has two).  CPN Tools
    # ignores them; so do we -- an arc to nowhere cannot be simulated or drawn.
    place_ids = {p.id for p in page.places}
    transition_ids = {t.id for t in page.transitions}
    page.arcs = [a for a in arcs if a.place_id in place_ids and a.transition_id in transition_ids]
    page.dropped_arcs = len(arcs) - len(page.arcs)
    return page


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def parse_cpn(xml_text: str, name: str = "Untitled") -> CPNet:
    """Parse ``.cpn`` XML held in a string."""
    # ``XMLParser`` with the default target does not expand external entities,
    # so a malicious DOCTYPE cannot read local files or hit the network.
    root = ElementTree.fromstring(xml_text)

    net = CPNet(name=name)
    net.source_tree = root

    cpnet = root.find("cpnet") if root.tag != "cpnet" else root
    if cpnet is None:
        raise ValueError("not a CPN Tools file: no <cpnet> element found")

    globbox = cpnet.find("globbox")
    if globbox is not None:
        _read_globbox(globbox, net)

    for page_element in cpnet.findall("page"):
        net.pages.append(_read_page(page_element))

    # Fusion sets are listed separately; each names the places it unifies.
    for fusion in cpnet.findall(".//fusion"):
        group = fusion.get("name", "")
        members = [e.get("idref", "") for e in fusion.findall("fusion_elm")]
        if group:
            net.fusion_sets[group] = members

    net.compile()
    return net


def read_cpn(path: str | Path) -> CPNet:
    """Load a ``.cpn`` file from disk.

    The model's name is taken from the file stem, which is what CPN Tools shows
    in its title bar.
    """
    file_path = Path(path)
    net = parse_cpn(file_path.read_text(encoding="utf-8", errors="replace"),
                    name=file_path.stem)
    return net
