"""English -> NCERT-style Hindi translation with resilient PDF-fragment handling."""
import json, os, re, time
from typing import Any
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None; types = None

QUESTION_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[\.\)]\s*")
MATH_TOKEN_RE = re.compile(r"__MATH_\d+__")
SECTION_RE = re.compile(r"^\s*(PHYSICS|CHEMISTRY|MATHEMATICS|BIOLOGY|BOTANY|ZOOLOGY)\s*$", re.I)

def _has_hindi(s): return any("\u0900" <= c <= "\u097F" for c in str(s or ""))
def _strip_fence(s):
    s=(s or "").strip()
    s=re.sub(r"^```(?:json|text)?\s*", "", s, flags=re.I)
    return re.sub(r"\s*```$", "", s).strip()
def _remove_qno(s): return QUESTION_RE.sub("", s or "", count=1).strip()
def _math_only(s): return not MATH_TOKEN_RE.sub("", s or "").strip()
def _fragment(s):
    v=MATH_TOKEN_RE.sub("", str(s or "")).strip()
    if not v: return True
    if len(v) <= 2 and re.fullmatch(r"[A-Za-z0-9()\[\]{}.,:;:+\-*/=<>%]+", v): return True
    return bool(re.fullmatch(r"(?:\(?[A-Da-d]\)?|[0-9]+|[ivxIVX]+|[\W_]+)", v))
def _transient(e):
    t=str(e).upper(); c=getattr(e,"code",None) or getattr(e,"status_code",None)
    return c in {408,429,500,502,503,504} or any(x in t for x in ("429","500","502","503","504","UNAVAILABLE","RESOURCE_EXHAUSTED"))
def _extract_json(s):
    raw=_strip_fence(s)
    try:return json.loads(raw)
    except Exception:pass
    for start in [raw.find("["),raw.find("{")]:
        if start<0: continue
        for end in range(len(raw),start,-1):
            try:return json.loads(raw[start:end])
            except Exception:pass
    return None

class DemoTranslator:
    def __init__(self, api_key=""):
        self.api_key=(api_key or os.getenv("GEMINI_API_KEY","")).strip()
        self.model=os.getenv("GEMINI_MODEL","gemini-3.7-flash").strip()
        self.fallback_model=os.getenv("GEMINI_FALLBACK_MODEL","gemini-3.6-flash").strip()
        self.client=genai.Client(api_key=self.api_key) if self.api_key and genai else None
    def _models(self):
        out=[]
        for m in (self.model,self.fallback_model,"gemini-3.5-flash","gemini-3.5-flash-lite"):
            if m and m not in out: out.append(m)
        return out
    def _call(self,model,prompt):
        last=None
        for a in range(3):
            try:
                return self.client.models.generate_content(model=model,contents=prompt,config=types.GenerateContentConfig(response_mime_type="text/plain"))
            except Exception as e:
                last=e
                if not _transient(e) or a==2: break
                time.sleep(2**a)
        raise last or RuntimeError("Gemini request failed")
    def _text(self,r):
        v=str(getattr(r,"text","") or "").strip()
        if v:return v
        parts=[]
        for c in getattr(r,"candidates",[]) or []:
            for p in getattr(getattr(c,"content",None),"parts",[]) or []:
                if getattr(p,"text",None): parts.append(str(p.text))
        return "\n".join(parts).strip()
    def _single(self, source):
        source=str(source or "").strip()
        if _math_only(source) or _fragment(source): return source
        clean="".join(ch for ch in source if ch in "\n\t" or ord(ch)>=32)
        prompts=[
            "Translate ONLY this competitive-exam text into formal NCERT-style Hindi. Output ONLY Hindi in Devanagari. Preserve every __MATH_n__ token, number, unit, symbol and line break exactly. Do not explain or solve.\nTEXT:\n"+clean,
            "Convert this English exam fragment to Hindi. Output only the translation, no quotes, no JSON. Preserve math tokens exactly.\n"+clean,
        ]
        for model in self._models():
            for prompt in prompts:
                try:
                    v=_strip_fence(self._text(self._call(model,prompt)))
                    if _has_hindi(v): return v
                except Exception: pass
        return ""
    def _batch(self, items):
        if not items:return {}
        result={}
        prompt='''Translate every item from English to formal NCERT-style Hindi. Return ONLY a JSON array. Every input id MUST appear exactly once. Use real Devanagari Hindi. Preserve __MATH_n__ tokens, numbers, symbols and line breaks. Do not solve or explain. INPUT:\n'''+json.dumps(items,ensure_ascii=False)
        for model in self._models():
            try:
                parsed=_extract_json(self._text(self._call(model,prompt)))
                if isinstance(parsed,dict): parsed=[parsed]
                if isinstance(parsed,list):
                    for x in parsed:
                        if isinstance(x,dict) and x.get("id") and _has_hindi(x.get("hindi","")):
                            result[str(x["id"])]=str(x["hindi"]).strip()
                if len(result)==len(items): return result
            except Exception: pass
        for item in items:
            if item["id"] not in result:
                v=self._single(item["text"])
                if v: result[item["id"]]=v
        for item in items:
            if item["id"] not in result and _fragment(item["text"]): result[item["id"]]=item["text"]
        return result
    def _merge(self,blocks):
        out=[]
        for b in blocks:
            if not out or b.get("type")!="text" or out[-1].get("type")!="text": out.append(b); continue
            p=out[-1]; pb=p.get("bbox",[0,0,0,0]); cb=b.get("bbox",[0,0,0,0])
            same=p.get("page")==b.get("page") and p.get("column")==b.get("column")
            gap=float(cb[1])-float(pb[3]); starts=bool(QUESTION_RE.match(str(b.get("text",""))))
            if same and not starts and gap<=8:
                p["text"]=(str(p.get("text","")).strip()+" "+str(b.get("text","")).strip()).strip()
                p["translation_source"]=(str(p.get("translation_source") or p.get("text","")).strip()+" "+str(b.get("translation_source") or b.get("text","")).strip()).strip()
                p["lines"]=list(p.get("lines",[]))+list(b.get("lines",[])); p["math_values"]=list(p.get("math_values",[]))+list(b.get("math_values",[]))
                p["bbox"]=[min(pb[0],cb[0]),min(pb[1],cb[1]),max(pb[2],cb[2]),max(pb[3],cb[3])]; continue
            out.append(b)
        return out
    def translate_document(self,document):
        questions=[]; current=None; section=None; counter=0
        def flush():
            nonlocal current
            if not current:return
            current["blocks"]=self._merge(current["blocks"]); queue=[]
            for b in current["blocks"]:
                if b.get("type")!="text":continue
                src=_remove_qno(b.get("translation_source") or b.get("text","")).strip()
                if not src: continue
                if _math_only(src) or _fragment(src): b["hindi"]=src; continue
                queue.append({"id":b.get("_translation_id"),"text":src})
            trans={}
            for i in range(0,len(queue),3): trans.update(self._batch(queue[i:i+3]))
            for b in current["blocks"]:
                if b.get("type")=="image": b["hindi"]=""; continue
                tid=b.get("_translation_id")
                if tid:
                    src=_remove_qno(b.get("translation_source") or b.get("text","")).strip()
                    b["hindi"]=(trans.get(tid) or self._single(src) or src).strip()
                b.pop("_translation_id",None)
            questions.append(current); current=None
        for page in document.get("pages",[]):
            for block in page.get("blocks",[]):
                text=str(block.get("text","") or "").strip()
                if block.get("type")=="image":
                    if current is not None: current["blocks"].append(dict(block))
                    continue
                if not text:continue
                if SECTION_RE.fullmatch(text): flush(); section=text.upper(); continue
                m=QUESTION_RE.match(text)
                if m:
                    flush(); current={"number":int(m.group(1)),"section":section,"blocks":[]}; section=None
                if current is None:continue
                b=dict(block); b["english"]=text; b["hindi"]=""; counter+=1; b["_translation_id"]=f"b{counter}"; current["blocks"].append(b)
        flush(); questions.sort(key=lambda q:int(q.get("number",0)))
        return {"questions":questions,"source":document}
