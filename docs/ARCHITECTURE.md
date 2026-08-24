# Architecture

```text
PDF
 │
 ├── text PDF ────────┐
 │                     │
 └── scanned PDF ─ OCR ─┐
                        ↓
                Layout / block model
                        ↓
              Question + option detector
                        ↓
               Formula protection
                        ↓
             NCERT Hindi translation
                        ↓
            Layout reconstruction
                        ↓
              Word DOCX + OMML math
```

## Planned upgrades

### 1. Vision/OCR
Use a vision-capable OCR stage for scanned papers and figures.

### 2. Layout model
Cluster blocks by x/y coordinates instead of reading only as a text stream.

### 3. Option model
Detect `(A)`, `(B)`, `(C)`, `(D)` and preserve their original columns.

### 4. Equation model
Convert recognized equation markup into OMML. Complex fractions, roots,
matrices, limits and multi-line equations should become editable Word equations.

### 5. NCERT glossary
Add a versioned glossary for Physics, Chemistry and Biology terminology.

### 6. Human review
Show English/Hindi side-by-side before DOCX export, allowing corrections.
