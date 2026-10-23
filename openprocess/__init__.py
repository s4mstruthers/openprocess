"""OpenProcess -- Coloured Petri Nets, Petri nets and process mining, on macOS, Windows and Linux.

A from-scratch implementation of the modelling, simulation and analysis parts
of CPN Tools / CPN IDE in pure Python, with a PySide6 desktop application.

Quick start::

    from openprocess import read_cpn, Simulator, StateSpace

    net = read_cpn("model.cpn")
    print(net.errors)                 # [] means it compiled cleanly

    simulator = Simulator(net, seed=0)
    simulator.run(100)
    print(simulator.marking.describe(net))

    print(StateSpace(net).generate().report())

Package layout
--------------
``openprocess.ml``
    The CPN ML subset: values, multisets, colour sets, lexer, parser, evaluator.
``openprocess.model``
    The net data model and the declaration compiler.
``openprocess.io``
    Reading and writing CPN Tools ``.cpn`` XML.
``openprocess.sim``
    Binding search and the simulator.
``openprocess.analysis``
    State space generation and the properties derived from it.
``openprocess.mining``
    Process mining: event logs (XES/CSV), discovery (α, Inductive Miner,
    Heuristics), conformance (token replay, alignments, precision), P/T nets,
    PNML, soundness.  See ``openprocess/mining/__init__.py``.
``openprocess.gui``
    The PySide6 desktop applications (optional; need the ``gui`` extra):
    the CPN editor (``openprocess.gui.app``) and OpenProcess Studio (``openprocess.gui.studio``).
"""

from .analysis.state_space import StateSpace
from .io.cpn_reader import parse_cpn, read_cpn
from .io.cpn_writer import to_xml_string, write_cpn
from .ml.multiset import Multiset, TimedMultiset
from .model.net import Arc, CPNet, Marking, Page, Place, Transition
from .sim.binding import BindingElement
from .sim.simulator import Simulator

#: The one place the version number is written (pyproject.toml, the update
#: check and packaging/build.py read it from here).  A release is tagged
#: "v" + this, e.g. v0.2.0.
__version__ = "0.12.0"

__all__ = [
    "Arc", "BindingElement", "CPNet", "Marking", "Multiset", "Page", "Place",
    "Simulator", "StateSpace", "TimedMultiset", "Transition",
    "parse_cpn", "read_cpn", "to_xml_string", "write_cpn",
]
