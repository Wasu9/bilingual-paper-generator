from backend.services.translator import DemoTranslator

def test_formula_protection():
    t = DemoTranslator()
    text = "The relation is v = u + at."
    protected, formulas = t.protect_formulas(text)
    assert formulas
    assert "v = u + at" in formulas[0]

def test_demo_translation_does_not_change_formula():
    t = DemoTranslator()
    out = t.translate_text("Energy is E = mc^2.")
    assert "E = mc^2" in out
