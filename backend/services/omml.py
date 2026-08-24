"""Small Word OMML helpers.

python-docx has no high-level equation API. These helpers insert Office Math
Markup Language (OMML) directly into the DOCX XML for common structures.
This is a foundation, not a full LaTeX engine.
"""

from lxml import etree

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = f"{{{M}}}"


def _r(text):
    r = etree.Element(NS + "r")
    t = etree.SubElement(r, NS + "t")
    t.text = text
    return r


def math_run(text):
    return _r(text)


def fraction(num, den):
    f = etree.Element(NS + "f")
    fpr = etree.SubElement(f, NS + "fPr")
    etree.SubElement(fpr, NS + "type", val="bar")
    num_el = etree.SubElement(f, NS + "num")
    num_el.append(_r(num))
    den_el = etree.SubElement(f, NS + "den")
    den_el.append(_r(den))
    return f


def superscript(base, sup):
    s = etree.Element(NS + "sSup")
    e = etree.SubElement(s, NS + "e")
    e.append(_r(base))
    sup_el = etree.SubElement(s, NS + "sup")
    sup_el.append(_r(sup))
    return s


def subscript(base, sub):
    s = etree.Element(NS + "sSub")
    e = etree.SubElement(s, NS + "e")
    e.append(_r(base))
    sub_el = etree.SubElement(s, NS + "sub")
    sub_el.append(_r(sub))
    return s
