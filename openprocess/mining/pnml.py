"""PNML: the ISO/IEC 15909-2 interchange format for Petri nets.

PNML is what ProM, PM4Py, WoPeD and most academic tools read and write, so
it is the way to move a discovered model between this app and anything else.

The subset we handle (the "P/T net" type)::

    <pnml>
      <net id="net1" type="http://www.pnml.org/version-2009/grammar/ptnet">
        <name><text>My net</text></name>
        <page id="page1">
          <place id="p1">
            <name><text>start</text></name>
            <initialMarking><text>1</text></initialMarking>
            <graphics><position x="10" y="20"/></graphics>
          </place>
          <transition id="t1">
            <name><text>register</text></name>
            <toolspecific tool="ProM" version="6.4" activity="$invisible$"/>
          </transition>
          <arc id="a1" source="p1" target="t1">
            <inscription><text>1</text></inscription>
          </arc>
        </page>
        <finalmarkings>                         <- ProM extension
          <marking><place idref="p9"><text>1</text></place></marking>
        </finalmarkings>
      </net>
    </pnml>

Silent transitions are marked with ProM's ``toolspecific`` element
``activity="$invisible$"``; that is the convention PM4Py follows too.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .petrinet import Marking, PetriNet

_INVISIBLE = "$invisible$"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _text_of(element: ET.Element | None) -> str | None:
    """The ``<text>`` content of a PNML label such as ``<name>``."""
    if element is None:
        return None
    text = _child(element, "text")
    return text.text.strip() if text is not None and text.text else None


def parse_pnml(text: str | bytes) -> PetriNet:
    root = ET.fromstring(text)
    net_element = root if _local(root.tag) == "net" else next(
        (e for e in root.iter() if _local(e.tag) == "net"), None)
    if net_element is None:
        raise ValueError("No <net> element found; is this a PNML file?")

    net = PetriNet(_text_of(_child(net_element, "name")) or net_element.get("id") or "Petri net")
    initial: dict[str, int] = {}
    arcs: list[tuple[str, str, int]] = []

    # Nodes may sit inside (nested) <page> elements or directly under <net>.
    for element in net_element.iter():
        tag = _local(element.tag)
        # <place idref=...> inside <finalmarkings> is a reference, not a node:
        # only elements with an id define places, transitions and arcs.
        if element.get("id") is None:
            continue
        if tag == "place":
            place = net.add_place(_text_of(_child(element, "name")) or element.get("id"),
                                  id=element.get("id"))
            place.position = _position(element)
            place.name_offset = _name_offset(element)
            tokens = _text_of(_child(element, "initialMarking"))
            if tokens and tokens.isdigit() and int(tokens) > 0:
                initial[place.id] = int(tokens)
        elif tag == "transition":
            name = _text_of(_child(element, "name")) or element.get("id")
            invisible = any(
                _local(sub.tag) == "toolspecific" and sub.get("activity") == _INVISIBLE
                for sub in element)
            transition = net.add_transition(None if invisible else name, name=name,
                                            id=element.get("id"))
            transition.position = _position(element)
            transition.name_offset = _name_offset(element)
        elif tag == "arc":
            weight_text = _text_of(_child(element, "inscription"))
            weight = int(weight_text) if weight_text and weight_text.isdigit() else 1
            graphics = _child(element, "graphics")
            points = []
            if graphics is not None:
                for sub in graphics:
                    if _local(sub.tag) == "position":
                        points.append((float(sub.get("x", 0)), float(sub.get("y", 0))))
            arcs.append((element.get("source"), element.get("target"), weight, points))

    for source, target, weight, points in arcs:
        net.add_arc(source, target, weight).points = points
    for element in net_element:
        if _local(element.tag) == "toolspecific" and element.get("tool") == "OpenProcess" \
                and element.get("namesOutside") == "true":
            net.info["names_outside"] = "true"
    for place_id in initial:
        if place_id not in net.places:
            raise ValueError(f"the initial marking names a place that is not in the net: {place_id}")
    if not initial:
        # No initial marking stored (a file from a tool that keeps none): for a
        # workflow net the obvious choice is one token in the source place, as
        # for the final marking below.  Without it every conformance check
        # would start from an empty net and report nothing fits.
        sources = net.source_places()
        if len(sources) == 1:
            initial = {sources[0]: 1}
    net.initial_marking = Marking(initial)

    final: dict[str, int] = {}
    finals = _child(net_element, "finalmarkings")
    if finals is not None:
        marking = _child(finals, "marking")
        if marking is not None:
            for place in marking:
                tokens = _text_of(place)
                if tokens and tokens.isdigit() and int(tokens) > 0:
                    final[place.get("idref")] = int(tokens)
    if final:
        for place_id in final:
            if place_id not in net.places:
                raise ValueError(f"the final marking names a place that is not in the net: {place_id}")
        net.final_marking = Marking(final)
    else:
        # No final marking stored: for a workflow net the obvious choice is
        # one token in the sink place.
        sinks = net.sink_places()
        if len(sinks) == 1:
            net.final_marking = Marking({sinks[0]: 1})
    return net


def _position(element: ET.Element) -> tuple[float, float] | None:
    graphics = _child(element, "graphics")
    position = _child(graphics, "position") if graphics is not None else None
    if position is None:
        return None
    try:
        return float(position.get("x", 0)), float(position.get("y", 0))
    except ValueError:
        return None


def _name_offset(element: ET.Element) -> tuple[float, float] | None:
    """``<name><graphics><offset x y/></graphics></name>``: where the label is."""
    name = _child(element, "name")
    graphics = _child(name, "graphics") if name is not None else None
    offset = _child(graphics, "offset") if graphics is not None else None
    if offset is None:
        return None
    try:
        return float(offset.get("x", 0)), float(offset.get("y", 0))
    except ValueError:
        return None


def read_pnml(path: str | Path) -> PetriNet:
    net = parse_pnml(Path(path).read_bytes())
    net.info.setdefault("source", str(path))
    return net


def pnml_string(net: PetriNet, positions: dict[str, tuple[float, float]] | None = None) -> str:
    """Serialise ``net``.  ``positions`` (e.g. from the layout) overrides stored ones."""
    positions = positions or {}

    def graphics(node_id: str, stored) -> str:
        point = positions.get(node_id, stored)
        if point is None:
            return ""
        return (f'<graphics><position x="{point[0]:.1f}" y="{point[1]:.1f}"/>'
                f'<dimension x="40" y="40"/></graphics>')

    def name(node) -> str:
        """The node's name, with the label position when one was set."""
        offset = getattr(node, "name_offset", None)
        where = (f'<graphics><offset x="{offset[0]:.1f}" y="{offset[1]:.1f}"/></graphics>'
                 if offset is not None else "")
        return f"<name><text>{escape(node.name)}</text>{where}</name>"

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<pnml>",
             f'  <net id="net1" type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel">',
             f"    <name><text>{escape(net.name)}</text></name>"]
    if net.info.get("names_outside") == "true":
        # OpenProcess's drawing option: names next to the nodes, not inside.
        lines.append('    <toolspecific tool="OpenProcess" version="1" namesOutside="true"/>')
    lines.append('    <page id="page1">')
    for place in net.places.values():
        tokens = net.initial_marking[place.id]
        marking = (f"<initialMarking><text>{tokens}</text></initialMarking>" if tokens else "")
        lines.append(f"      <place id={quoteattr(place.id)}>{name(place)}"
                     f"{marking}{graphics(place.id, place.position)}</place>")
    for transition in net.transitions.values():
        tool = ('<toolspecific tool="ProM" version="6.4" activity="$invisible$" '
                'localNodeID="" />' if transition.silent else "")
        lines.append(f"      <transition id={quoteattr(transition.id)}>{name(transition)}{tool}"
                     f"{graphics(transition.id, transition.position)}</transition>")
    for index, arc in enumerate(net.arcs, 1):
        inscription = (f"<inscription><text>{arc.weight}</text></inscription>"
                       if arc.weight != 1 else "")
        bends = "".join(f'<position x="{x:.1f}" y="{y:.1f}"/>' for x, y in arc.points)
        bends = f"<graphics>{bends}</graphics>" if bends else ""
        lines.append(f"      <arc id=\"arc{index}\" source={quoteattr(arc.source)} "
                     f"target={quoteattr(arc.target)}>{inscription}{bends}</arc>")
    lines.append("    </page>")
    if net.final_marking:
        lines.append("    <finalmarkings><marking>")
        for place_id, tokens in net.final_marking.items():
            lines.append(f"      <place idref={quoteattr(place_id)}><text>{tokens}</text></place>")
        lines.append("    </marking></finalmarkings>")
    lines += ["  </net>", "</pnml>", ""]
    return "\n".join(lines)


def write_pnml(net: PetriNet, path: str | Path,
               positions: dict[str, tuple[float, float]] | None = None) -> None:
    Path(path).write_text(pnml_string(net, positions), encoding="utf-8")
